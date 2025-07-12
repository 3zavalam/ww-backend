import os
import stripe
from flask import Blueprint, request, jsonify

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")

verify_bp = Blueprint("verify_bp", __name__)

@verify_bp.route("/api/verify-session", methods=["POST"])
def verify_session():
    data = request.get_json()
    session_id = data.get("session_id")

    if not session_id:
        return jsonify({"success": False, "error": "Missing session_id"}), 400

    try:
        # Recuperar sesión desde Stripe
        session = stripe.checkout.Session.retrieve(session_id)

        if session["payment_status"] == "paid":
            print(f"✅ Verificación de sesión exitosa para {session_id}")
            return jsonify({"success": True})
        else:
            print(f"❌ La sesión {session_id} no está pagada (estado: {session['payment_status']})")
            return jsonify({"success": False, "reason": "Not paid"}), 400

    except Exception as e:
        print(f"⚠️ Error verificando sesión: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 400
