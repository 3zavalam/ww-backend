import os
import uuid
import json
import time
import threading
import boto3
from botocore.exceptions import ClientError
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename

SQS_QUEUE_URL = os.getenv("SQS_QUEUE_URL")  # SQS queue URL
SQS_DLQ_URL = os.getenv("SQS_DLQ_URL")  # Dead letter queue URL (optional)
LAUNCH_TEMPLATE_ID = os.getenv("LAUNCH_TEMPLATE_ID", "lt-0fe372ebe8a9e42af")
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "/tmp/winnerway/uploads")
STATIC_FOLDER = os.getenv("STATIC_FOLDER", "/tmp/winnerway/static")
GPU_INSTANCE_ID = os.getenv("GPU_INSTANCE_ID")  # e.g. i-xxxxxxxx
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
S3_BUCKET = os.getenv("S3_BUCKET")  # e.g. winnerway-uploads
S3_RESULTS_BUCKET = os.getenv("S3_RESULTS_BUCKET", S3_BUCKET)  # For storing results
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "https://www.winnerway.pro,http://localhost:8080"
).split(",")
IDLE_SHUTDOWN_MIN = int(os.getenv("IDLE_SHUTDOWN_MIN", 5))  # minutes
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", 20))  # seconds between SQS polls

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(STATIC_FOLDER, exist_ok=True)

sqs = boto3.client("sqs", region_name=AWS_REGION)
ec2 = boto3.client("ec2", region_name=AWS_REGION)
s3 = boto3.client("s3", region_name=AWS_REGION)

app = Flask(__name__, static_folder=STATIC_FOLDER)
CORS(app, origins=ALLOWED_ORIGINS)

job_status = {}
job_results = {}
last_activity = {"time": time.time()}


def get_gpu_instance_info():
    if not GPU_INSTANCE_ID:
        return {"error": "GPU_INSTANCE_ID not configured"}

    try:
        response = ec2.describe_instances(InstanceIds=[GPU_INSTANCE_ID])
        if not response['Reservations']:
            return {"error": "GPU instance not found"}

        instance = response['Reservations'][0]['Instances'][0]

        return {
            "instance_id": GPU_INSTANCE_ID,
            "state": instance['State']['Name'],
            "state_reason": instance.get('StateReason', {}).get('Message', ''),
            "instance_type": instance['InstanceType'],
            "launch_time": instance.get('LaunchTime').isoformat() if instance.get('LaunchTime') else None,
            "private_ip": instance.get('PrivateIpAddress'),
            "public_ip": instance.get('PublicIpAddress'),
            "availability_zone": instance['Placement']['AvailabilityZone'],
            "lifecycle": instance.get('InstanceLifecycle', 'on-demand'),
            "spot_instance_request_id": instance.get('SpotInstanceRequestId')
        }
    except ClientError as e:
        return {"error": str(e)}


def enqueue(payload: dict) -> str:
    job_id = uuid.uuid4().hex
    payload["id"] = job_id

    try:
        response = sqs.send_message(
            QueueUrl=SQS_QUEUE_URL,
            MessageBody=json.dumps(payload),
            MessageAttributes={
                'JobId': {
                    'StringValue': job_id,
                    'DataType': 'String'
                },
                'JobType': {
                    'StringValue': 'video_analysis',
                    'DataType': 'String'
                }
            }
        )

        job_status[job_id] = "queued"
        last_activity["time"] = time.time()
        app.logger.info(f"Job {job_id} queued to SQS with MessageId: {response['MessageId']}")
        return job_id

    except ClientError as e:
        app.logger.error(f"Failed to enqueue job {job_id}: {e}")
        raise


def maybe_start_gpu():
    try:
        app.logger.info("Checking GPU instance status...")
        resp = ec2.describe_instances(
            InstanceIds=[GPU_INSTANCE_ID],
            Filters=[{"Name": "instance-state-name", "Values": ["running", "pending"]}]
        )
        if not resp["Reservations"]:
            app.logger.info("Starting GPU instance...")
            launch_new_spot_instance()
            ec2.get_waiter("instance_running").wait(InstanceIds=[GPU_INSTANCE_ID])
            app.logger.info("GPU instance is now running")

        last_activity["time"] = time.time()

    except ClientError as e:
        app.logger.error(f"Failed to start GPU instance: {e}")
        raise


def maybe_stop_gpu():
    try:
        idle_time = time.time() - last_activity["time"]
        if idle_time > IDLE_SHUTDOWN_MIN * 60:
            app.logger.info("No jobs for a while—stopping GPU instance...")

            # First, try to stop the instance normally
            try:
                ec2.terminate_instances(InstanceIds=[GPU_INSTANCE_ID])
                #ec2.stop_instances(InstanceIds=[GPU_INSTANCE_ID])
                last_activity["time"] = time.time()
                app.logger.info("GPU instance stopped successfully")

            except ClientError as stop_error:
                # Check if it's the specific Spot instance error
                if "UnsupportedOperation" in str(stop_error) and "one-time Spot Instance request" in str(stop_error):
                    app.logger.warning("Cannot stop one-time Spot instance, terminating instead...")

                    # Terminate the one-time Spot instance
                    ec2.terminate_instances(InstanceIds=[GPU_INSTANCE_ID])
                    last_activity["time"] = time.time()
                    app.logger.info("One-time Spot instance terminated successfully")

                else:
                    # Re-raise if it's a different error
                    raise stop_error

    except ClientError as e:
        app.logger.error(f"Failed to stop/terminate GPU instance: {e}")
    except Exception as e:
        app.logger.error(f"Unexpected error in maybe_stop_gpu: {e}")


def upload_to_s3(local_path: str, key: str) -> str:
    """Upload file to S3 with better error handling and verification"""
    try:
        # Check if local file exists
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file not found: {local_path}")

        file_size = os.path.getsize(local_path)
        app.logger.info(f"Uploading {local_path} ({file_size} bytes) to s3://{S3_BUCKET}/{key}")

        # Upload file
        s3.upload_file(local_path, S3_BUCKET, key)

        # Verify upload
        try:
            response = s3.head_object(Bucket=S3_BUCKET, Key=key)
            uploaded_size = response['ContentLength']
            if uploaded_size != file_size:
                raise Exception(f"Size mismatch: local={file_size}, s3={uploaded_size}")
        except ClientError as e:
            raise Exception(f"Upload verification failed: {e}")

        s3_uri = f"s3://{S3_BUCKET}/{key}"
        app.logger.info(f"Successfully uploaded and verified: {s3_uri}")
        return s3_uri

    except Exception as e:
        app.logger.error(f"Failed to upload to S3: {e}")
        raise


def check_job_results():
    while True:
        try:
            # Check for result files in S3
            for job_id in list(job_status.keys()):
                if job_status.get(job_id) in ["queued", "processing"]:
                    result_key = f"results/{job_id}.json"
                    try:
                        # Try to get the result file
                        response = s3.get_object(Bucket=S3_RESULTS_BUCKET, Key=result_key)
                        result_data = json.loads(response['Body'].read().decode('utf-8'))

                        # Update job status
                        job_status[job_id] = "done"
                        job_results[job_id] = result_data
                        last_activity["time"] = time.time()
                        app.logger.info(f"Job {job_id} completed")

                        # Clean up result file from S3
                        s3.delete_object(Bucket=S3_RESULTS_BUCKET, Key=result_key)

                    except ClientError as e:
                        if e.response['Error']['Code'] != 'NoSuchKey':
                            app.logger.error(f"Error checking result for job {job_id}: {e}")

            time.sleep(POLL_INTERVAL)

        except Exception as e:
            app.logger.error(f"Error in result checking loop: {e}")
            time.sleep(POLL_INTERVAL)


@app.route("/upload", methods=["POST"])
def upload():
    video = request.files.get("video")
    email = request.form.get("email")
    handed = request.form.get("handedness", "right")
    stroke = request.form.get("stroke_type", "forehand")

    if not video or not email:
        return jsonify({"error": "Missing video file or email"}), 400

    # Check if S3_BUCKET is configured
    if not S3_BUCKET:
        return jsonify({"error": "S3_BUCKET not configured"}), 500

    try:
        # Save file locally first
        base, ext = os.path.splitext(secure_filename(video.filename))
        file_name = f"{base}_{uuid.uuid4().hex}{ext}"
        save_path = os.path.join(UPLOAD_FOLDER, file_name)

        app.logger.info(f"Saving video to: {save_path}")
        video.save(save_path)

        # Check if file was actually saved
        if not os.path.exists(save_path):
            return jsonify({"error": "Failed to save file locally"}), 500

        file_size = os.path.getsize(save_path)
        app.logger.info(f"Saved upload to {save_path}, size: {file_size} bytes")

        # Upload to S3 with better error handling
        s3_key = f"uploads/{file_name}"
        app.logger.info(f"Uploading to S3: bucket={S3_BUCKET}, key={s3_key}")

        try:
            # Upload with explicit content type
            content_type = video.content_type or 'video/mp4'
            s3.upload_file(
                save_path,
                S3_BUCKET,
                s3_key,
                ExtraArgs={
                    'ContentType': content_type,
                    'Metadata': {
                        'original_filename': video.filename,
                        'upload_timestamp': str(int(time.time()))
                    }
                }
            )

            # Verify the upload by checking if object exists
            try:
                s3.head_object(Bucket=S3_BUCKET, Key=s3_key)
                app.logger.info(f"Successfully uploaded and verified: s3://{S3_BUCKET}/{s3_key}")
            except ClientError as e:
                app.logger.error(f"Upload verification failed: {e}")
                return jsonify({"error": "S3 upload verification failed"}), 500

        except ClientError as e:
            app.logger.error(f"S3 upload failed: {e}")
            # Don't clean up local file if S3 upload failed
            return jsonify({"error": f"S3 upload failed: {str(e)}"}), 500

        s3_path = f"s3://{S3_BUCKET}/{s3_key}"

        # Enqueue the job
        job_id = enqueue({
            "s3_path": s3_path,
            "email": email,
            "stroke_type": stroke,
            "handedness": handed,
            "original_filename": video.filename,
            "s3_key": s3_key,
            "file_size": file_size
        })

        maybe_start_gpu()

        # Clean up local file only after successful S3 upload
        try:
            os.remove(save_path)
            app.logger.info(f"Cleaned up local file: {save_path}")
        except OSError as e:
            app.logger.warning(f"Failed to clean up local file: {e}")

        return jsonify({
            "job_id": job_id,
            "s3_path": s3_path,
            "s3_key": s3_key,
            "file_size": file_size
        }), 202

    except Exception as e:
        app.logger.error(f"Upload failed: {e}")
        # Try to clean up local file if it exists
        if 'save_path' in locals() and os.path.exists(save_path):
            try:
                os.remove(save_path)
            except OSError:
                pass
        return jsonify({"error": f"Upload failed: {str(e)}"}), 500


@app.route("/status/<job_id>")
def status(job_id):
    state = job_status.get(job_id, "unknown")

    if state == "done" and job_id in job_results:
        result = job_results[job_id]
        return jsonify({"status": state, "result": result})

    return jsonify({"status": state})


@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory(STATIC_FOLDER, filename)


@app.route("/health")
def health():
    return jsonify({"status": "healthy", "timestamp": time.time()})


@app.route("/start", methods=["POST"])
def start():
    if not GPU_INSTANCE_ID:
        return jsonify({"error": "GPU_INSTANCE_ID not configured"}), 400

    try:
        # Get current instance info
        info = get_gpu_instance_info()

        # If instance doesn't exist or is terminated, we need to launch a new Spot instance
        if "error" in info or info.get("state") == "terminated":
            app.logger.info("Instance terminated or doesn't exist, launching new Spot instance...")
            return launch_new_spot_instance()

        current_state = info["state"]

        # Check if already running or starting
        if current_state == "running":
            return jsonify({
                "message": "GPU instance is already running",
                "instance_info": info
            })

        if current_state == "pending":
            return jsonify({
                "message": "GPU instance is already starting",
                "instance_info": info
            })

        # Check if in a state that can't be started
        if current_state in ["stopping", "shutting-down"]:
            return jsonify({
                "error": f"Cannot start instance in state: {current_state}. Please wait for it to fully stop.",
                "instance_info": info
            }), 400

        # Try to start the instance (works for regular instances and persistent Spot instances)
        app.logger.info(f"Starting GPU instance {GPU_INSTANCE_ID}")

        try:
            start_response = ec2.start_instances(InstanceIds=[GPU_INSTANCE_ID])

            # Update last activity
            last_activity["time"] = time.time()

            return jsonify({
                "message": "GPU instance start command sent successfully",
                "instance_id": GPU_INSTANCE_ID,
                "previous_state": current_state,
                "starting_instances": start_response.get("StartingInstances", []),
                "note": "Instance may take 1-2 minutes to fully start"
            })

        except ClientError as start_error:
            # If start fails (e.g., for terminated Spot instances), try launching new one
            if "InvalidInstanceID.NotFound" in str(start_error) or "terminated" in str(start_error).lower():
                app.logger.info("Start failed, instance likely terminated. Launching new Spot instance...")
                return launch_new_spot_instance()
            else:
                raise start_error

    except ClientError as e:
        app.logger.error(f"Failed to start GPU instance: {e}")
        return jsonify({"error": str(e)}), 500


def launch_new_spot_instance():
    try:
        app.logger.info(f"Launching new Spot instance using template {LAUNCH_TEMPLATE_ID}...")

        # Launch instance using launch template with Spot pricing
        response = ec2.run_instances(
            MinCount=1,
            MaxCount=1,
            LaunchTemplate={
                'LaunchTemplateId': LAUNCH_TEMPLATE_ID,
                'Version': '1'
            },
            InstanceMarketOptions={
                'MarketType': 'spot',
                'SpotOptions': {
                    'MaxPrice': '0.50',
                    'SpotInstanceType': 'one-time',
                    'InstanceInterruptionBehavior': 'terminate'
                }
            }
        )

        new_instance_id = response['Instances'][0]['InstanceId']

        # Update the global instance ID
        global GPU_INSTANCE_ID
        GPU_INSTANCE_ID = new_instance_id

        # Update last activity
        last_activity["time"] = time.time()

        app.logger.info(f"New Spot instance launched: {new_instance_id}")

        return jsonify({
            "message": "New Spot instance launched successfully using launch template",
            "instance_id": new_instance_id,
            "launch_template": LAUNCH_TEMPLATE_ID,
            "note": "Instance may take 1-2 minutes to fully start"
        })

    except Exception as e:
        app.logger.error(f"Failed to launch new Spot instance: {e}")
        return jsonify({"error": f"Failed to launch new Spot instance: {str(e)}"}), 500


@app.route("/stop", methods=["POST"])
def stop():
    if not GPU_INSTANCE_ID:
        return jsonify({"error": "GPU_INSTANCE_ID not configured"}), 400

    try:
        # Get current instance info
        info = get_gpu_instance_info()
        if "error" in info:
            return jsonify(info), 500

        current_state = info["state"]

        # Check if already stopped or stopping
        if current_state in ["stopped", "stopping"]:
            return jsonify({
                "message": f"GPU instance is already {current_state}",
                "instance_info": info
            })

        # Check if in a state that can't be stopped
        if current_state in ["pending", "shutting-down", "terminated"]:
            return jsonify({
                "error": f"Cannot stop instance in state: {current_state}",
                "instance_info": info
            }), 400

        # Stop the instance
        app.logger.info(f"Stopping GPU instance {GPU_INSTANCE_ID}")
        stop_response = ec2.terminate_instances(InstanceIds=[GPU_INSTANCE_ID])

        # Update last activity
        last_activity["time"] = time.time()

        return jsonify({
            "message": "GPU instance stop command sent successfully",
            "instance_id": GPU_INSTANCE_ID,
            "previous_state": current_state,
            "stopping_instances": stop_response.get("StoppingInstances", []),
            "note": "Instance may take 1-2 minutes to fully stop"
        })

    except ClientError as e:
        app.logger.error(f"Failed to stop GPU instance: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/istatus", methods=["GET"])
def istatus():
    if not GPU_INSTANCE_ID:
        return jsonify({"error": "GPU_INSTANCE_ID not configured"}), 400

    try:
        info = get_gpu_instance_info()

        if "error" in info:
            return jsonify(info), 500

        # Add additional useful information
        info["last_activity"] = {
            "timestamp": last_activity["time"],
            "time_ago_seconds": int(time.time() - last_activity["time"]),
            "formatted": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(last_activity["time"]))
        }

        info["idle_shutdown"] = {
            "enabled": True,
            "idle_threshold_minutes": IDLE_SHUTDOWN_MIN,
            "time_until_shutdown_seconds": max(0, IDLE_SHUTDOWN_MIN * 60 - (time.time() - last_activity["time"]))
        }

        # Add queue information
        try:
            queue_attrs = sqs.get_queue_attributes(
                QueueUrl=SQS_QUEUE_URL,
                AttributeNames=['ApproximateNumberOfMessages', 'ApproximateNumberOfMessagesNotVisible']
            )
            info["sqs_status"] = {
                "messages_in_queue": int(queue_attrs['Attributes']['ApproximateNumberOfMessages']),
                "messages_in_flight": int(queue_attrs['Attributes']['ApproximateNumberOfMessagesNotVisible'])
            }
        except Exception as e:
            info["sqs_status"] = {"error": str(e)}

        return jsonify(info)

    except ClientError as e:
        app.logger.error(f"Failed to get GPU instance status: {e}")
        return jsonify({"error": str(e)}), 500


def cleanup_old_jobs():
    while True:
        try:
            current_time = time.time()
            cutoff_time = current_time - (24 * 60 * 60)  # 24 hours

            # Clean up jobs older than 24 hours
            jobs_to_remove = []
            for job_id in job_status.keys():
                # This is a simple cleanup - in production, you'd want to track job creation time
                if len(job_status) > 1000:  # Arbitrary limit
                    jobs_to_remove.append(job_id)

            for job_id in jobs_to_remove[:100]:  # Remove oldest 100
                job_status.pop(job_id, None)
                job_results.pop(job_id, None)

            time.sleep(3600)  # Run every hour

        except Exception as e:
            app.logger.error(f"Error in cleanup loop: {e}")
            time.sleep(3600)


def idle_monitor():
    while True:
        try:
            time.sleep(60)  # Check every minute
            maybe_stop_gpu()
        except Exception as e:
            app.logger.error(f"Error in idle monitor: {e}")
            time.sleep(60)


if __name__ == "__main__":
    if not SQS_QUEUE_URL:
        raise ValueError("SQS_QUEUE_URL environment variable is required")

    # Start background threads
    result_thread = threading.Thread(target=check_job_results, daemon=True)
    result_thread.start()

    cleanup_thread = threading.Thread(target=cleanup_old_jobs, daemon=True)
    cleanup_thread.start()

    idle_thread = threading.Thread(target=idle_monitor, daemon=True)
    idle_thread.start()

    app.logger.info("Starting Flask app with SQS integration...")
    app.run(host="0.0.0.0", port=5050)