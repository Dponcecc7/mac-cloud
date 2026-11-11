# -*- coding: utf-8 -*-
"""Resetea la contraseña de un usuario existente (identificado por email).

Uso:
    python reset_password.py correo@dominio.com "NuevaContraseña123"

No hardcodea ningún correo ni contraseña: ambos se pasan como argumentos,
para no dejar un secreto en el repo. Requiere que DATABASE_URL (o el resto
de variables de .env) apunte a la base de datos real -- corre esto donde
esas variables ya estén configuradas (tu entorno local con .env, o el
servidor), nunca pegues la cadena de conexión en el chat.
"""
import sys

from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from models import Usuario


def main():
    if len(sys.argv) != 3:
        print('Uso: python reset_password.py correo@dominio.com "NuevaContraseña"')
        sys.exit(1)

    email = sys.argv[1].strip().lower()
    password = sys.argv[2]

    if len(password) < 8:
        print("La contraseña debe tener al menos 8 caracteres.")
        sys.exit(1)

    app = create_app()
    with app.app_context():
        usuario = Usuario.query.filter_by(email=email).first()
        if usuario is None:
            print(f"No existe ningún usuario con el correo {email}.")
            sys.exit(1)

        usuario.password_hash = generate_password_hash(password)
        db.session.commit()
        print(f"Contraseña actualizada para {email} (rol={usuario.rol}).")


if __name__ == "__main__":
    main()
