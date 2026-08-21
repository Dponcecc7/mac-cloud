# Desplegar mac_cloud gratis: Render (app) + Aiven (PostgreSQL)

> PythonAnywhere ya no sirve para esto: desde enero 2026 el acceso a base de datos en cuentas nuevas pasó a ser de pago. Esta combinación sigue siendo 100% gratis y sin tarjeta: **Render** hospeda la app Flask, **Aiven** da la base PostgreSQL gratis sin límite de tiempo (se eligió Postgres en vez de MySQL — igual de capaz para este proyecto, sin necesidad técnica real de que fuera MySQL específicamente). El código está armado para conectarse a cualquier base vía `DATABASE_URL`, así que este cambio de motor no afectó nada más que el driver.

## 1. Crear la base de datos PostgreSQL en Aiven
1. Entra a https://aiven.io/ → crea cuenta (sin tarjeta).
2. Crea un servicio **PostgreSQL**, plan **Free**.
3. Cuando esté listo (unos minutos), andá a la pestaña **Overview**/**Connection information** del servicio y copia: `Host`, `Port`, `User`, `Password`, `Database name`.
4. Con eso arma la cadena de conexión (reemplaza cada parte):
   ```
   postgresql+psycopg://USUARIO:PASSWORD@HOST:PUERTO/NOMBRE_BD?sslmode=require
   ```
   (Aiven exige SSL — el `?sslmode=require` al final es importante, sin eso la conexión falla).

## 2. Subir el código a GitHub
Render despliega conectándose a un repo de GitHub (auto-deploy en cada push). Si no tenés uno para esto todavía:
```bash
cd "mac_cloud"
git init
git add .
git commit -m "Esqueleto mac_cloud: auth + roles"
```
Después creá un repositorio nuevo (puede ser privado) en https://github.com/new y seguí las instrucciones que te da GitHub para conectar tu carpeta local (`git remote add origin ...` + `git push`).

## 3. Crear el servicio web en Render
1. Entra a https://render.com/ → crea cuenta (sin tarjeta) → conectá tu cuenta de GitHub.
2. **New** → **Web Service** → elegí el repo que acabas de subir.
3. Configuración:
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn wsgi:application`
   - **Plan**: Free
4. En **Environment** (variables de entorno), agregá:
   - `SECRET_KEY` = (la clave que ya generamos)
   - `DATABASE_URL` = la cadena de conexión de Aiven del paso 1
5. **Create Web Service** — Render instala dependencias y levanta la app sola. Te da una URL tipo `https://mac-cloud-xxxx.onrender.com`.

## 4. Crear el primer usuario admin
No hace falta consola remota — como Aiven es accesible desde cualquier lado, corré `seed_admin.py` **desde tu propia PC**, apuntando a la base de Aiven:
```powershell
cd "mac_cloud"
$env:SECRET_KEY = "la-misma-clave-que-pusiste-en-render"
$env:DATABASE_URL = "postgresql+psycopg://USUARIO:PASSWORD@HOST:PUERTO/NOMBRE_BD?sslmode=require"
.\.venv\Scripts\python.exe seed_admin.py
```
Esto crea la tabla `usuarios` sola (`db.create_all()`) y te pide el correo/contraseña del primer admin — no hace falta correr SQL a mano.

## 5. Probar
Entra a `https://tu-app.onrender.com/health` — debe responder `{"status": "ok", "db": true}`. Después `/login` con el admin que creaste.

## Cosas a tener en cuenta
- **Render free duerme la app tras 15 min sin uso** — el primer acceso después de un rato inactivo tarda ~60 segundos en "despertar". Para una herramienta interna de uso esporádico está bien; si más adelante molesta, se puede pasar a un plan pago chico solo para eso.
- Cada `git push` a tu repo redeploya sola la app en Render — así se actualiza el código de acá en adelante (nada de subir archivos a mano).
- A diferencia de PythonAnywhere, Render **no restringe las llamadas salientes** — cuando lleguen las Fases 2/3 del roadmap (Athena, Microsoft Graph), no debería hacer falta cambiar de proveedor por esa razón.
