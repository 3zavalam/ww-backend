import os
import stripe
from flask import Blueprint, request, jsonify, current_app

# Asumiendo que tienes un cliente de Supabase configurado así:
try:
    from integrations.supabase import supabase
except ImportError:
    supabase = None

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")

# IDs de precios de Stripe desde variables de entorno
PRICE_CHALLENGE_ONEOFF = os.environ.get("PRICE_CHALLENGE_ONEOFF")
PRICE_PRO_MONTHLY = os.environ.get("PRICE_PRO_MONTHLY")

stripe_bp = Blueprint("stripe_bp", __name__)

def get_or_create_customer(user_id: str, email: str) -> str:
    """
    Busca un customer_id en la base de datos.
    Si no existe, lo crea en Stripe y lo guarda en la base de datos.
    """
    if not supabase:
        raise ConnectionError("Supabase client no disponible.")

    # 1. Buscar en BD
    user_data = supabase.table("users").select("stripe_customer_id").eq("id", user_id).single().execute()
    
    customer_id = user_data.data.get("stripe_customer_id")
    
    if customer_id:
        return customer_id

    # 2. Si no existe, crear en Stripe
    try:
        new_customer = stripe.Customer.create(
            email=email,
            metadata={"user_id": user_id}
        )
        customer_id = new_customer.id

        # 3. Guardar en BD para futuros usos
        supabase.table("users").update({"stripe_customer_id": customer_id}).eq("id", user_id).execute()
        
        return customer_id
    except Exception as e:
        current_app.logger.error(f"Error creando customer en Stripe para {user_id}: {e}")
        raise

def create_customer_from_email(email: str) -> str:
    """
    Crea un customer en Stripe usando solo email (para usuarios no loggeados)
    """
    try:
        new_customer = stripe.Customer.create(
            email=email,
            # No incluimos user_id porque aún no existe en nuestra DB
        )
        return new_customer.id
    except Exception as e:
        current_app.logger.error(f"Error creando customer en Stripe para {email}: {e}")
        raise


@stripe_bp.route("/api/checkout", methods=["POST"])
def create_checkout_session():
    """
    Crea una sesión de Checkout de Stripe.
    Recibe: { "price_id": "price_xyz", "user_id": "uuid", "email": "user@test.com" }
    """
    data = request.get_json()
    price_id = data.get("price_id")
    user_id = data.get("user_id")
    email = data.get("email")

    if not all([price_id, user_id, email]):
        return jsonify({"error": "Faltan parámetros (price_id, user_id, email)"}), 400

    # Determina el modo de pago
    if price_id == PRICE_CHALLENGE_ONEOFF:
        mode = "payment"
        line_items = [{"price": price_id, "quantity": 1}]
        subscription_data = {}
    elif price_id == PRICE_PRO_MONTHLY:
        mode = "subscription"
        line_items = [{"price": price_id, "quantity": 1}]
        subscription_data = {"trial_period_days": 28}
    else:
        return jsonify({"error": "Price ID no válido"}), 400

    try:
        # Obtener o crear el ID de cliente de Stripe
        customer_id = get_or_create_customer(user_id, email)

        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=line_items,
            mode=mode,
            subscription_data=subscription_data,
            success_url="http://localhost:8080/success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url="http://localhost:8080/", # URL de vuelta si cancela
            metadata={
                "user_id": user_id,
                "price_id": price_id
            }
        )
        return jsonify({"url": session.url})
    except Exception as e:
        current_app.logger.error(f"Error creando sesión de checkout: {e}")
        return jsonify({"error": "No se pudo crear la sesión de pago"}), 500

@stripe_bp.route("/api/checkout-public", methods=["POST"])
def create_checkout_session_public():
    """
    Crea una sesión de Checkout de Stripe para usuarios NO loggeados.
    Recibe: { "price_id": "price_xyz", "email": "user@test.com" }
    """
    data = request.get_json()
    price_id = data.get("price_id")
    email = data.get("email")

    if not all([price_id, email]):
        return jsonify({"error": "Faltan parámetros (price_id, email)"}), 400

    # Determina el modo de pago
    if price_id == PRICE_CHALLENGE_ONEOFF:
        mode = "payment"
        line_items = [{"price": price_id, "quantity": 1}]
        subscription_data = {}
    elif price_id == PRICE_PRO_MONTHLY:
        mode = "subscription"
        line_items = [{"price": price_id, "quantity": 1}]
        subscription_data = {"trial_period_days": 28}
    else:
        return jsonify({"error": "Price ID no válido"}), 400

    try:
        # Crear customer en Stripe usando solo email
        customer_id = create_customer_from_email(email)

        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=line_items,
            mode=mode,
            subscription_data=subscription_data,
            success_url="http://localhost:8080/success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url="http://localhost:8080/", # Vuelve a landing si cancela
            metadata={
                "price_id": price_id
                # NO incluimos user_id porque el usuario aún no existe en nuestra DB
            }
        )
        return jsonify({"url": session.url})
    except Exception as e:
        current_app.logger.error(f"Error creando sesión de checkout público: {e}")
        return jsonify({"error": "No se pudo crear la sesión de pago"}), 500

@stripe_bp.route("/api/create-portal-session", methods=["POST"])
def create_portal_session():
    """
    Crea una sesión del Portal de Cliente de Stripe para que un usuario
    pueda gestionar su suscripción.
    Recibe: { "customer_id": "cus_xyz" }
    """
    data = request.get_json()
    customer_id = data.get("customer_id")

    if not customer_id:
        return jsonify({"error": "Falta el ID del cliente de Stripe"}), 400

    try:
        # La URL a la que volverá el usuario después de usar el portal
        return_url = "https://www.winnerway.pro/" 

        portal_session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
        return jsonify({"url": portal_session.url})
    except Exception as e:
        current_app.logger.error(f"Error creando sesión del portal de cliente: {e}")
        return jsonify({"error": "No se pudo crear la sesión del portal"}), 500