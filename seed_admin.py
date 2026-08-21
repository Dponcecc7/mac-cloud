# -*- coding: utf-8 -*-
"""Crea el primer usuario admin -- corrida única, interactiva (nunca hardcodea
la contraseña en el repo). Uso: python seed_admin.py"""
import getpass

from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db
from models import Usuario


def main():
    app = create_app()
    with app.app_context():
        db.create_all()

        print("Crear el primer usuario administrador.")
        email = input("Correo: ").strip().lower()
        if Usuario.query.filter_by(email=email).first():
            print(f"Ya existe un usuario con el correo {email}. Nada que hacer.")
            return

        password = getpass.getpass("Contraseña: ")
        confirmar = getpass.getpass("Confirmar contraseña: ")
        if password != confirmar:
            print("Las contraseñas no coinciden. Intenta de nuevo.")
            return
        if len(password) < 8:
            print("La contraseña debe tener al menos 8 caracteres. Intenta de nuevo.")
            return

        admin = Usuario(email=email, password_hash=generate_password_hash(password), rol="admin")
        db.session.add(admin)
        db.session.commit()
        print(f"Usuario admin {email} creado correctamente.")


if __name__ == "__main__":
    main()
