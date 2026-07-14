#!/bin/bash
# Ejecutar una sola vez en el servidor como usuario debian (con sudo)
# Idempotente: si Python 3.11 o el conf de nginx ya existen, no los recrea.
set -e

APP_DIR="/opt/smartassist"

echo "==> Actualizando paquetes..."
sudo apt-get update --allow-releaseinfo-change || true

# Python 3.11 solo si no existe ya en el servidor
if ! command -v python3.11 &> /dev/null; then
    echo "==> Instalando dependencias de compilación y nginx..."
    sudo apt-get install -y nginx build-essential libssl-dev zlib1g-dev \
        libncurses5-dev libreadline-dev libsqlite3-dev libgdbm-dev \
        libbz2-dev libexpat1-dev liblzma-dev libffi-dev uuid-dev

    echo "==> Compilando Python 3.11 (tarda ~5 min)..."
    cd /tmp
    if [ ! -f "Python-3.11.9.tgz" ]; then
        wget -q https://www.python.org/ftp/python/3.11.9/Python-3.11.9.tgz
    fi
    tar xzf Python-3.11.9.tgz
    cd Python-3.11.9
    ./configure --enable-optimizations --prefix=/usr/local --quiet
    make -j$(nproc)
    sudo make altinstall
    cd ~
else
    echo "==> Python 3.11 ya está instalado, saltando compilación."
fi

echo "==> Creando directorio de la aplicación..."
sudo mkdir -p "$APP_DIR"
sudo chown debian:debian "$APP_DIR"

echo "==> Creando entorno virtual Python 3.11..."
/usr/local/bin/python3.11 -m venv "$APP_DIR/.venv"

echo "==> Instalando dependencias Python..."
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> Configurando nginx..."
# El conf nuevo incluye los location blocks /sr/ y /sa/ + n8n en un solo server block.
# Deshabilitamos el conf antiguo smartretain para que no haya server blocks duplicados.
sudo cp "$APP_DIR/deploy/nginx-smartassist.conf" /etc/nginx/sites-available/smartassist
sudo ln -sf /etc/nginx/sites-available/smartassist /etc/nginx/sites-enabled/smartassist
sudo rm -f /etc/nginx/sites-enabled/smartretain
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx

echo "==> Configurando servicio systemd..."
sudo cp "$APP_DIR/deploy/smartassist.service" /etc/systemd/system/smartassist.service
sudo systemctl daemon-reload
sudo systemctl enable smartassist
sudo systemctl start smartassist

echo ""
echo "===================================================="
echo " Setup completado."
echo " Recuerda crear /opt/smartassist/.env con:"
echo "   OPENAI_API_KEY=..."
echo "   DB_HOST=..."
echo "   DB_PORT=..."
echo "   DB_NAME=SmartAssist"
echo "   DB_USER=..."
echo "   DB_PASSWORD=..."
echo ""
echo " Luego ejecuta desde tu Mac: python3 setup_db.py"
echo "===================================================="
