import os
import uuid
import json
import time
import threading
import boto3
from botocore.exceptions import ClientError
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

SQS_QUEUE_URL = os.getenv("SQS_QUEUE_URL")  # SQS queue URL
SQS_DLQ_URL = os.getenv("SQS_DLQ_URL")  # Dead letter queue URL (optional)
LAUNCH_TEMPLATE_ID = os.getenv("LAUNCH_TEMPLATE_ID", "lt-0fe372ebe8a9e42af")
STATIC_FOLDER = os.getenv("STATIC_FOLDER", "/tmp/winnerway/static")
GPU_INSTANCE_ID = None  # Will be set from environment or created dynamically
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
S3_BUCKET = os.getenv("S3_BUCKET")  # e.g. winnerway-uploads
S3_RESULTS_BUCKET = os.getenv("S3_RESULTS_BUCKET", S3_BUCKET)  # For storing results
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "https://www.winnerway.pro,http://localhost:8080"
).split(",")
IDLE_SHUTDOWN_MIN = int(os.getenv("IDLE_SHUTDOWN_MIN", 5))  # minutes
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", 20))  # seconds between SQS polls

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
    global GPU_INSTANCE_ID
    if not GPU_INSTANCE_ID:
        app.logger.info("No GPU instance configured, launching new one...")
        launch_new_spot_instance()
        return
    
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
    global GPU_INSTANCE_ID
    if not GPU_INSTANCE_ID:
        return
    
    try:
        idle_time = time.time() - last_activity["time"]
        if idle_time > IDLE_SHUTDOWN_MIN * 60:
            app.logger.info("No jobs for a while—stopping GPU instance...")

            try:
                # Apaga la instancia (no la destruye)
                ec2.stop_instances(InstanceIds=[GPU_INSTANCE_ID])
                last_activity["time"] = time.time()
                app.logger.info("GPU instance stopped successfully")
                
                # Evita nuevos intentos en el loop
                GPU_INSTANCE_ID = None

            except ClientError as stop_error:
                app.logger.error("Failed to stop GPU: %s", stop_error)
                return

    except ClientError as e:
        app.logger.error(f"Failed to stop/terminate GPU instance: {e}")
    except Exception as e:
        app.logger.error(f"Unexpected error in maybe_stop_gpu: {e}")


@app.route("/upload-url", methods=["POST"])
def generate_upload_url():
    """Genera presigned URL para upload directo a S3"""
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400
    
    email = data.get("email")
    stroke_type = data.get("stroke_type", "forehand")
    handedness = data.get("handedness", "right")
    
    if not email:
        return jsonify({"error": "Missing email"}), 400
    
    if not S3_BUCKET:
        return jsonify({"error": "S3_BUCKET not configured"}), 500
    
    try:
        # Generar clave única para S3
        file_uuid = uuid.uuid4().hex
        s3_key = f"uploads/{file_uuid}.mp4"
        
        # Generar presigned POST URL
        presigned_post = s3.generate_presigned_post(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Fields={
                "Content-Type": "video/mp4"
            },
            Conditions=[
                {"Content-Type": "video/mp4"},
                ["content-length-range", 1, 400 * 1024 * 1024]  # 1 byte a 400MB
            ],
            ExpiresIn=300  # 5 minutos
        )
        
        app.logger.info(f"Generated presigned URL for {email}, key: {s3_key}")
        
        return jsonify({
            "presigned": presigned_post,
            "s3_key": s3_key
        }), 200
        
    except Exception as e:
        app.logger.error(f"Failed to generate presigned URL: {e}")
        return jsonify({"error": "Failed to generate upload URL"}), 500


@app.route("/notify", methods=["POST"])
def notify_upload_complete():
    """Recibe notificación de upload exitoso y encola el job"""
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400
    
    s3_key = data.get("s3_key")
    email = data.get("email")
    stroke_type = data.get("stroke_type", "forehand")
    handedness = data.get("handedness", "right")
    original_filename = data.get("original_filename", "video.mp4")
    file_size = data.get("file_size", 0)
    
    if not all([s3_key, email]):
        return jsonify({"error": "Missing s3_key or email"}), 400
    
    if not S3_BUCKET:
        return jsonify({"error": "S3_BUCKET not configured"}), 500
    
    try:
        # Verificar que el objeto existe en S3
        try:
            s3.head_object(Bucket=S3_BUCKET, Key=s3_key)
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return jsonify({"error": "File not found in S3"}), 404
            raise
        
        # Construir s3_path para el worker
        s3_path = f"s3://{S3_BUCKET}/{s3_key}"
        
        # Encolar el job (igual que antes)
        job_id = enqueue({
            "s3_path": s3_path,
            "email": email,
            "stroke_type": stroke_type,
            "handedness": handedness,
            "original_filename": original_filename,
            "s3_key": s3_key,
            "file_size": file_size
        })
        
        # Arrancar GPU sin bloquear
        def start_gpu_async():
            try:
                maybe_start_gpu()
            except Exception as e:
                app.logger.error(f"Error starting GPU: {e}")
        
        threading.Thread(target=start_gpu_async, daemon=True).start()
        
        app.logger.info(f"Job {job_id} queued for s3_path: {s3_path}")
        
        return jsonify({
            "job_id": job_id,
            "status": "queued",
            "message": "Processing started"
        }), 202
        
    except Exception as e:
        app.logger.error(f"Failed to notify upload: {e}")
        return jsonify({"error": "Failed to process notification"}), 500


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


def launch_new_spot_instance():
    try:
        app.logger.info(f"Launching new Spot instance using template {LAUNCH_TEMPLATE_ID}...")

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
        global GPU_INSTANCE_ID
        GPU_INSTANCE_ID = new_instance_id
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


def cleanup_old_jobs():
    while True:
        try:
            current_time = time.time()
            cutoff_time = current_time - (24 * 60 * 60)

            jobs_to_remove = []
            for job_id in job_status.keys():
                if len(job_status) > 1000:
                    jobs_to_remove.append(job_id)

            for job_id in jobs_to_remove[:100]:
                job_status.pop(job_id, None)
                job_results.pop(job_id, None)

            time.sleep(3600)

        except Exception as e:
            app.logger.error(f"Error in cleanup loop: {e}")
            time.sleep(3600)


def idle_monitor():
    while True:
        try:
            time.sleep(300)           # cada 5 min
            maybe_stop_gpu()
        except Exception as e:
            app.logger.error(f"Error in idle monitor: {e}")
            time.sleep(60)


if __name__ == "__main__":
    if not SQS_QUEUE_URL:
        raise ValueError("SQS_QUEUE_URL environment variable is required")
    
    # Initialize GPU_INSTANCE_ID from environment
    GPU_INSTANCE_ID = os.getenv("GPU_INSTANCE_ID")

    # Start background threads
    result_thread = threading.Thread(target=check_job_results, daemon=True)
    result_thread.start()

    cleanup_thread = threading.Thread(target=cleanup_old_jobs, daemon=True)
    cleanup_thread.start()

    idle_thread = threading.Thread(target=idle_monitor, daemon=True)
    idle_thread.start()

    app.logger.info("Starting Flask app with SQS integration...")
    app.run(host="0.0.0.0", port=5050)