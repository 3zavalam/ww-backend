import os
import stripe
from flask import Blueprint, request, jsonify, current_app
from integrations.supabase import supabase

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
webhook_bp = Blueprint("webhook_bp", __name__)

# IDs de precios de Stripe desde variables de entorno
PRICE_CHALLENGE_ONEOFF = os.environ.get("PRICE_CHALLENGE_ONEOFF")
PRICE_PRO_MONTHLY = os.environ.get("PRICE_PRO_MONTHLY")

@webhook_bp.route("/api/stripe/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature")
    secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    if not secret:
        current_app.logger.error("STRIPE_WEBHOOK_SECRET no configurado.")
        return "Webhook secret no configurado", 500

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except ValueError:
        return "Payload inválido", 400
    except stripe.error.SignatureVerificationError:
        return "Firma inválida", 400

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        handle_checkout_session_completed(data)
    elif event_type in ["customer.subscription.updated", "customer.subscription.deleted"]:
        handle_subscription_change(data)
    else:
        current_app.logger.info(f"Evento no manejado: {event_type}")

    return jsonify({"status": "success"}), 200

def get_or_create_user_from_customer(customer_id):
    """
    Obtiene el customer de Stripe y crea/encuentra el usuario en nuestra DB
    """
    try:
        # Obtener datos del customer de Stripe
        customer = stripe.Customer.retrieve(customer_id)
        email = customer.email
        
        if not email:
            current_app.logger.error(f"Customer {customer_id} no tiene email")
            return None
            
        # Buscar usuario existente por email
        existing_user = supabase.table("users").select("id").eq("email", email).execute()
        
        if existing_user.data:
            user_id = existing_user.data[0]["id"]
            current_app.logger.info(f"Usuario existente encontrado: {user_id} para {email}")
            
            # Migrar videos de email a user_id si existen
            migrate_videos_response = supabase.table("videos").update(
                {"user_id": user_id, "email": None}
            ).eq("email", email).is_("user_id", None).execute()
            
            if migrate_videos_response.data:
                current_app.logger.info(f"Migrados {len(migrate_videos_response.data)} videos para {email}")
                
            return user_id
        else:
            # Crear nuevo usuario
            new_user_data = {
                "email": email,
                "plan_code": "free"
            }
            
            # Agregar nombre si está disponible
            if customer.name:
                new_user_data["name"] = customer.name
                
            new_user_response = supabase.table("users").insert(new_user_data).execute()
            user_id = new_user_response.data[0]["id"]
            current_app.logger.info(f"Nuevo usuario creado: {user_id} para {email}")
            
            # Migrar videos de email a user_id
            migrate_videos_response = supabase.table("videos").update(
                {"user_id": user_id, "email": None}
            ).eq("email", email).is_("user_id", None).execute()
            
            if migrate_videos_response.data:
                current_app.logger.info(f"Migrados {len(migrate_videos_response.data)} videos para {email}")
            
            return user_id
            
    except Exception as e:
        current_app.logger.error(f"Error creando/encontrando usuario para customer {customer_id}: {e}")
        return None

def handle_checkout_session_completed(session):
    customer_id = session.get("customer")
    price_id = session.get("metadata", {}).get("price_id")
    
    if not customer_id:
        current_app.logger.warning("Checkout completado sin customer_id.")
        return
        
    # Obtener o crear usuario desde el customer de Stripe
    user_id = get_or_create_user_from_customer(customer_id)
    
    if not user_id:
        current_app.logger.error("No se pudo obtener user_id para el checkout")
        return

    if price_id == PRICE_CHALLENGE_ONEOFF:
        # Lógica para el reto
        try:
            # 1. Actualizar plan del usuario
            supabase.table("users").update({"plan_code": "challenge"}).eq("id", user_id).execute()
            
            # 2. Crear entrada en la tabla de retos
            supabase.table("challenges").insert({
                "user_id": user_id,
                "start_date": "now()",
                "week": 1,
                "checklist": "[]", # Checklist vacío inicialmente
                "status": "active"
            }).execute()
            current_app.logger.info(f"Reto iniciado para usuario {user_id}")
        except Exception as e:
            current_app.logger.error(f"Error al activar reto para {user_id}: {e}")

    elif price_id == PRICE_PRO_MONTHLY:
        # La suscripción se crea, pero la manejamos con customer.subscription.updated
        # para tener una única fuente de verdad.
        # Aquí solo guardamos la info de la suscripción inicial.
        stripe_sub_id = session.get("subscription")
        if stripe_sub_id:
            sync_subscription(stripe_sub_id)


def handle_subscription_change(subscription):
    sync_subscription(subscription["id"])


def sync_subscription(stripe_subscription_id):
    """
    Obtiene los datos de una suscripción de Stripe y los sincroniza
    con la base de datos local (tablas subscriptions y users).
    """
    try:
        sub = stripe.Subscription.retrieve(stripe_subscription_id)
        customer_id = sub.customer
        
        # Obtener o crear usuario desde el customer
        user_id = get_or_create_user_from_customer(customer_id)
        
        if not user_id:
            current_app.logger.error(f"No se pudo obtener user_id para la suscripción {stripe_subscription_id}")
            return
            
        subscription_data = {
            "user_id": user_id,
            "stripe_subscription_id": sub.id,
            "status": sub.status,
            "current_period_end": f"to_timestamp({sub.current_period_end})",
            "plan_code": "pro" if sub.status in ["trialing", "active"] else "free",
        }

        # Insertar o actualizar la suscripción
        supabase.table("subscriptions").upsert(subscription_data, on_conflict="stripe_subscription_id").execute()

        # Actualizar el plan del usuario
        new_plan = "pro" if sub.status in ["trialing", "active"] else "free"
        supabase.table("users").update({"plan_code": new_plan}).eq("id", user_id).execute()
        
        current_app.logger.info(f"Suscripción {sub.id} para usuario {user_id} sincronizada. Nuevo plan: {new_plan}")

    except Exception as e:
        current_app.logger.error(f"Error sincronizando suscripción {stripe_subscription_id}: {e}")
