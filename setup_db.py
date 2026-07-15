"""
Script para crear la BBDD Coca-Cola Field Sales Assist y cargar datos mock B2B.
Ejecutar una sola vez: python setup_db.py

IMPORTANTE: este script crea/puebla el schema definido en DB_NAME (.env).
Debe apuntar a un schema NUEVO y separado (ej. 'cocacola_fieldsales'), nunca
al schema original de SMART-assist.
"""

import os
import random
from datetime import date, datetime, timedelta
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

DB_HOST     = os.getenv("DB_HOST")
DB_PORT     = int(os.getenv("DB_PORT", 3306))
DB_NAME     = os.getenv("DB_NAME", "cocacola_fieldsales")
DB_USER     = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

DB_CONFIG = {
    "host":     DB_HOST,
    "port":     DB_PORT,
    "database": DB_NAME,
    "user":     DB_USER,
    "password": DB_PASSWORD,
}

# ── Definicion de tablas ─────────────────────────────────────────────────────

CREATE_EMPRESAS_CLIENTES = """
CREATE TABLE IF NOT EXISTS empresas_clientes (
    id                      INT AUTO_INCREMENT PRIMARY KEY,
    codigo_cliente          VARCHAR(20)  NOT NULL UNIQUE,
    nombre_comercial        VARCHAR(150) NOT NULL,
    razon_social            VARCHAR(150),
    canal                   VARCHAR(30)  NOT NULL,
    tipo_distribucion       VARCHAR(20)  NOT NULL DEFAULT 'indirecto',
    tamano_canal            VARCHAR(20)  NOT NULL DEFAULT 'mediano',
    ciudad                  VARCHAR(80),
    zona                    VARCHAR(80),
    condicion_pago_habitual VARCHAR(20)  NOT NULL DEFAULT 'contado',
    vendedor_asignado       VARCHAR(100),
    fecha_alta              DATE         NOT NULL,
    activo                  TINYINT(1)   NOT NULL DEFAULT 1,
    notas                   TEXT
);
"""

CREATE_PRODUCTOS = """
CREATE TABLE IF NOT EXISTS productos (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    codigo_sku        VARCHAR(30)   NOT NULL UNIQUE,
    nombre            VARCHAR(150)  NOT NULL,
    categoria         VARCHAR(40)   NOT NULL,
    presentacion_tipo VARCHAR(20)   NOT NULL,
    formato           VARCHAR(30)   NOT NULL,
    litros            DECIMAL(6,3)  NOT NULL,
    precio_lista      DECIMAL(10,2) NOT NULL,
    activo            TINYINT(1)    NOT NULL DEFAULT 1,
    descripcion       TEXT
);
"""

CREATE_PEDIDOS = """
CREATE TABLE IF NOT EXISTS pedidos (
    numero_pedido              VARCHAR(20) PRIMARY KEY,
    empresa_cliente_id         INT NOT NULL,
    fecha_pedido                DATE NOT NULL,
    estado                     VARCHAR(20) NOT NULL DEFAULT 'solicitado',
    canal_venta                VARCHAR(30) NOT NULL,
    condicion_pago             VARCHAR(20) NOT NULL,
    vendedor                   VARCHAR(100),
    descuento_aplicado_pct     DECIMAL(5,2) NOT NULL DEFAULT 0,
    subtotal                   DECIMAL(12,2) NOT NULL DEFAULT 0,
    total                      DECIMAL(12,2) NOT NULL DEFAULT 0,
    notas                      TEXT,
    fecha_actualizacion_estado DATETIME,
    FOREIGN KEY (empresa_cliente_id) REFERENCES empresas_clientes(id)
);
"""

CREATE_DETALLE_PEDIDO = """
CREATE TABLE IF NOT EXISTS detalle_pedido (
    id                    INT AUTO_INCREMENT PRIMARY KEY,
    numero_pedido         VARCHAR(20)   NOT NULL,
    producto_id           INT           NOT NULL,
    cantidad              INT           NOT NULL DEFAULT 1,
    precio_unitario       DECIMAL(10,2) NOT NULL,
    descuento_pct         DECIMAL(5,2)  NOT NULL DEFAULT 0,
    precio_neto_unitario  DECIMAL(10,2) NOT NULL,
    subtotal_linea        DECIMAL(12,2) NOT NULL,
    FOREIGN KEY (numero_pedido) REFERENCES pedidos(numero_pedido),
    FOREIGN KEY (producto_id)   REFERENCES productos(id)
);
"""

CREATE_POLITICAS_DESCUENTO = """
CREATE TABLE IF NOT EXISTS politicas_descuento (
    id                      INT AUTO_INCREMENT PRIMARY KEY,
    canal                   VARCHAR(30)  NOT NULL,
    tamano_canal            VARCHAR(20),
    condicion_pago          VARCHAR(20)  NOT NULL,
    volumen_min_litros      DECIMAL(10,2) NOT NULL DEFAULT 0,
    volumen_max_litros      DECIMAL(10,2),
    descuento_pct           DECIMAL(5,2)  NOT NULL,
    condiciones_adicionales VARCHAR(255),
    prioridad               INT NOT NULL DEFAULT 0,
    activo                  TINYINT(1) NOT NULL DEFAULT 1,
    INDEX idx_politica_lookup (canal, condicion_pago, activo)
);
"""

CREATE_GESTIONES_POSVENTA = """
CREATE TABLE IF NOT EXISTS gestiones_posventa (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    numero_gestion      VARCHAR(20) NOT NULL UNIQUE,
    numero_pedido       VARCHAR(20),
    empresa_cliente_id  INT NOT NULL,
    tipo                VARCHAR(30) NOT NULL,
    descripcion         TEXT NOT NULL,
    estado              VARCHAR(20) NOT NULL DEFAULT 'abierto',
    prioridad           VARCHAR(10) NOT NULL DEFAULT 'media',
    canal_reporte       VARCHAR(20) NOT NULL DEFAULT 'chat',
    vendedor            VARCHAR(100),
    fecha_apertura      DATETIME NOT NULL,
    fecha_cierre        DATETIME,
    resolucion          TEXT,
    FOREIGN KEY (numero_pedido) REFERENCES pedidos(numero_pedido) ON DELETE SET NULL,
    FOREIGN KEY (empresa_cliente_id) REFERENCES empresas_clientes(id)
);
"""

CREATE_PERFILES = """
CREATE TABLE IF NOT EXISTS perfiles (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    nombre      VARCHAR(100) NOT NULL,
    empresa     VARCHAR(100) NOT NULL,
    logo_url    VARCHAR(500),
    activo      TINYINT(1)   NOT NULL DEFAULT 0,
    created_at  TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_perfil_nombre (nombre)
);
"""

CREATE_ONTOLOGIAS = """
CREATE TABLE IF NOT EXISTS ontologias (
    id        INT AUTO_INCREMENT PRIMARY KEY,
    nombre    VARCHAR(100) NOT NULL,
    version   VARCHAR(10)  NOT NULL DEFAULT '1.0',
    contenido LONGTEXT     NOT NULL,
    activo    TINYINT(1)   NOT NULL DEFAULT 1,
    perfil_id INT          NULL,
    UNIQUE KEY uq_perfil_nombre_version (perfil_id, nombre, version),
    CONSTRAINT fk_ontologia_perfil FOREIGN KEY (perfil_id) REFERENCES perfiles(id) ON DELETE CASCADE
);
"""

# Ontologias que pertenecen a un perfil (vs globales como autopilot-*)
PROFILE_ONTOLOGIES = ("system-prompt", "ontologia-procedimientos", "ontologia-descuentos", "ontologia-faq")

# ── Catalogo de canales ──────────────────────────────────────────────────────

CANALES = ["tradicional", "moderno", "on_premise", "off_premise", "mayorista", "ecommerce", "institucional_horeca", "vending"]
TIPOS_DISTRIBUCION = ["directo", "indirecto"]
TAMANOS_CANAL = ["pequeno", "mediano", "grande"]
CONDICIONES_PAGO = ["contado", "credito"]
_ESTADOS_PEDIDO = ["solicitado", "en_revision", "aprobado", "facturado", "entregado", "rechazado", "cancelado"]

random.seed(42)

# ── Datos mock: productos (SKUs Coca-Cola) ──────────────────────────────────

PRODUCTOS_MOCK = [
    # codigo_sku, nombre, categoria, presentacion_tipo, formato, litros, precio_lista, descripcion
    ("CC-355-RET",  "Coca-Cola Original",        "gaseosas",     "retornable",    "botella 355ml",  0.355, 450.00, "Gaseosa cola clasica, botella retornable de vidrio."),
    ("CC-500-NR",   "Coca-Cola Original",        "gaseosas",     "no_retornable", "botella 500ml",  0.500, 620.00, "Gaseosa cola clasica, botella PET individual."),
    ("CC-1500-NR",  "Coca-Cola Original",        "gaseosas",     "no_retornable", "botella 1.5L",   1.500, 1150.00, "Gaseosa cola clasica, formato familiar PET."),
    ("CC-2250-RET", "Coca-Cola Original",        "gaseosas",     "retornable",    "botella 2.25L",  2.250, 1400.00, "Gaseosa cola clasica, formato hogar retornable."),
    ("CCZ-500-NR",  "Coca-Cola Zero",            "gaseosas",     "no_retornable", "botella 500ml",  0.500, 620.00, "Gaseosa cola sin azucar, botella PET individual."),
    ("CCZ-1500-NR", "Coca-Cola Zero",            "gaseosas",     "no_retornable", "botella 1.5L",   1.500, 1150.00, "Gaseosa cola sin azucar, formato familiar PET."),
    ("SPR-500-NR",  "Sprite",                    "gaseosas",     "no_retornable", "botella 500ml",  0.500, 600.00, "Gaseosa lima-limon, botella PET individual."),
    ("SPR-1500-NR", "Sprite",                    "gaseosas",     "no_retornable", "botella 1.5L",   1.500, 1120.00, "Gaseosa lima-limon, formato familiar PET."),
    ("FAN-500-NR",  "Fanta Naranja",              "gaseosas",     "no_retornable", "botella 500ml",  0.500, 600.00, "Gaseosa sabor naranja, botella PET individual."),
    ("FAN-1500-NR", "Fanta Naranja",              "gaseosas",     "no_retornable", "botella 1.5L",   1.500, 1120.00, "Gaseosa sabor naranja, formato familiar PET."),
    ("LAT-354-CJ",  "Coca-Cola Original Lata",    "gaseosas",     "no_retornable", "lata 354ml x24",  8.496, 9800.00, "Pack de 24 latas, formato para on premise / vending."),
    ("AGB-500-NR",  "Villa Cristal Agua sin gas", "aguas",        "no_retornable", "botella 500ml",  0.500, 380.00, "Agua mineral sin gas, botella individual."),
    ("AGC-500-NR",  "Villa Cristal Agua con gas", "aguas",        "no_retornable", "botella 500ml",  0.500, 400.00, "Agua mineral con gas, botella individual."),
    ("AGB-2000-NR", "Villa Cristal Agua sin gas", "aguas",        "no_retornable", "bidon 2L",       2.000, 950.00, "Agua mineral sin gas, formato familiar."),
    ("SAB-500-NR",  "Quatro Pomelo",              "saborizadas",  "no_retornable", "botella 500ml",  0.500, 580.00, "Bebida saborizada citrica, botella individual."),
    ("SAB-1500-NR", "Quatro Pomelo",              "saborizadas",  "no_retornable", "botella 1.5L",   1.500, 1080.00, "Bebida saborizada citrica, formato familiar."),
    ("JUG-200-NR",  "Cepita Naranja",             "jugos",        "no_retornable", "botella 200ml",  0.200, 350.00, "Jugo de naranja, formato individual."),
    ("JUG-1000-NR", "Cepita Naranja",             "jugos",        "no_retornable", "botella 1L",     1.000, 980.00, "Jugo de naranja, formato familiar."),
    ("ISO-500-NR",  "Aquarius Isotonica",         "isotonicas",   "no_retornable", "botella 500ml",  0.500, 650.00, "Bebida isotonica, botella individual."),
    ("ENE-473-NR",  "Monster Energy",             "energizantes", "no_retornable", "lata 473ml",     0.473, 1350.00, "Bebida energizante, lata individual."),
]

# ── Datos mock: empresas cliente (cuentas B2B por canal) ────────────────────

_NOMBRES_NEGOCIO = [
    "Don Manuel", "La Esquina", "El Sol", "San Martin", "Del Centro", "La Familia",
    "El Progreso", "Norte", "Sur", "La Union", "El Ceibo", "Las Flores", "Central",
    "El Rincon", "La Estrella", "Del Puerto", "San Jose", "La Plaza", "El Faro",
    "Del Parque", "La Merced", "El Mirador", "Santa Rita", "La Loma", "El Trebol",
]

_ZONAS = [
    ("Buenos Aires", "CABA Norte"), ("Buenos Aires", "CABA Sur"), ("Buenos Aires", "GBA Oeste"),
    ("Cordoba", "Capital"), ("Rosario", "Centro"), ("Mendoza", "Gran Mendoza"),
    ("La Plata", "Centro"), ("Mar del Plata", "Costa"), ("Tucuman", "Capital"),
    ("Salta", "Capital"),
]

_CANAL_TEMPLATES = {
    "tradicional":           ["Kiosco {n}", "Almacen {n}", "Autoservicio {n}", "Despensa {n}"],
    "moderno":               ["Supermercado {n}", "Hipermercado {n}", "Autoservicio Mayorista {n}"],
    "on_premise":            ["Bar {n}", "Restaurante {n}", "Boliche {n}", "Cerveceria {n}"],
    "off_premise":           ["Bebidas para Llevar {n}", "Drugstore 24hs {n}", "Vinoteca {n}"],
    "mayorista":             ["Distribuidora {n} S.A.", "Mayorista {n}"],
    "ecommerce":             ["{n} Market Online", "Tienda Online {n}"],
    "institucional_horeca":  ["Hotel {n}", "Catering {n}", "Comedor Corporativo {n}", "Complejo de Eventos {n}"],
    "vending":               ["Vending Edificio {n}", "Maquinas Expendedoras {n}"],
}

_CANAL_WEIGHTS = {
    "tradicional": 30, "moderno": 15, "on_premise": 15, "off_premise": 8,
    "mayorista": 8, "ecommerce": 6, "institucional_horeca": 10, "vending": 8,
}

_VENDEDORES = ["Martina Ruiz", "Lucas Fernandez", "Sofia Alvarez", "Nicolas Diaz", "Camila Torres"]


def _gen_empresas_clientes(n=36):
    empresas = []
    canales_pool = list(_CANAL_WEIGHTS.keys())
    pesos = list(_CANAL_WEIGHTS.values())
    used_names = set()
    for i in range(1, n + 1):
        canal = random.choices(canales_pool, weights=pesos)[0]
        template = random.choice(_CANAL_TEMPLATES[canal])
        while True:
            nombre = template.format(n=random.choice(_NOMBRES_NEGOCIO))
            if nombre not in used_names:
                used_names.add(nombre)
                break
        razon_social = f"{nombre.split(' ')[-1]} S.R.L." if canal not in ("mayorista", "ecommerce") else nombre

        # Distribucion directa mas probable en canales de mayor volumen (moderno, mayorista, institucional)
        if canal in ("moderno", "mayorista", "institucional_horeca"):
            tipo_dist = random.choices(TIPOS_DISTRIBUCION, weights=[70, 30])[0]
        else:
            tipo_dist = random.choices(TIPOS_DISTRIBUCION, weights=[20, 80])[0]

        if canal in ("moderno", "mayorista"):
            tamano = random.choices(TAMANOS_CANAL, weights=[10, 30, 60])[0]
        elif canal in ("institucional_horeca",):
            tamano = random.choices(TAMANOS_CANAL, weights=[15, 45, 40])[0]
        else:
            tamano = random.choices(TAMANOS_CANAL, weights=[55, 35, 10])[0]

        condicion_habitual = random.choices(CONDICIONES_PAGO, weights=[65, 35])[0] if canal not in ("moderno", "mayorista", "institucional_horeca") \
            else random.choices(CONDICIONES_PAGO, weights=[25, 75])[0]

        ciudad, zona = random.choice(_ZONAS)
        fecha_alta = date(2019, 1, 1) + timedelta(days=random.randint(0, (date(2025, 12, 31) - date(2019, 1, 1)).days))
        vendedor = random.choice(_VENDEDORES)
        codigo = f"CLI-{i:04d}"

        empresas.append((
            codigo, nombre, razon_social, canal, tipo_dist, tamano,
            ciudad, zona, condicion_habitual, vendedor, fecha_alta.isoformat(), 1, None,
        ))
    return empresas


EMPRESAS_CLIENTES_MOCK = _gen_empresas_clientes(36)


# ── Motor de politicas de descuento (curado a mano) ─────────────────────────

POLITICAS_DESCUENTO_MOCK = [
    # canal, tamano_canal, condicion_pago, vol_min_L, vol_max_L, descuento_pct, condiciones_adicionales, prioridad
    ("tradicional", None,      "contado",  0,     50,     3.0,  None,                                             10),
    ("tradicional", None,      "contado",  50,    150,    5.0,  None,                                             10),
    ("tradicional", None,      "credito",  0,     150,    2.0,  "Sujeto a linea de credito aprobada",            10),
    ("moderno",     None,      "contado",  0,     500,    6.0,  None,                                             10),
    ("moderno",     None,      "contado",  500,   2000,   10.0, "Requiere exhibicion acordada",                   10),
    ("moderno",     "grande",  "credito",  0,     2000,   7.0,  "Contrato comercial anual",                       20),
    ("on_premise",  None,      "contado",  0,     300,    8.0,  "Aplica a formatos retornables",                  10),
    ("on_premise",  None,      "credito",  0,     300,    5.0,  None,                                             10),
    ("off_premise", None,      "contado",  0,     100,    4.0,  None,                                             10),
    ("mayorista",   None,      "contado",  1000,  None,   15.0, "Volumen minimo de reventa",                       10),
    ("mayorista",   None,      "credito",  1000,  None,   12.0, "Sujeto a linea de credito aprobada",             10),
    ("ecommerce",   None,      "contado",  0,     None,   3.0,  "Precio online estandar",                         10),
    ("institucional_horeca", None, "contado", 0,   2000,   10.0, None,                                            10),
    ("institucional_horeca", None, "credito", 2000, None,  14.0, "Requiere contrato anual",                        20),
    ("vending",     None,      "contado",  0,     None,   5.0,  None,                                             10),
    ("todos",       None,      "contado",  0,     None,   0.0,  "Piso por defecto — sin politica especifica",     -1),
    ("todos",       None,      "credito",  0,     None,   0.0,  "Piso por defecto — sin politica especifica",     -1),
]


def _match_politica(canal, tamano, condicion_pago, volumen_litros, politicas=POLITICAS_DESCUENTO_MOCK):
    """Replica en Python la misma logica de seleccion determinista que usara la tool
    consultar_politica_descuento en chatbot.py: filtra por canal/condicion_pago/volumen,
    ordena por especificidad (tamano_canal exacto > canal exacto > prioridad) y toma la primera."""
    candidatas = []
    for c, tam, cond, vmin, vmax, pct, cond_ad, prio in politicas:
        if cond != condicion_pago:
            continue
        if c != canal and c != "todos":
            continue
        if tam is not None and tam != tamano:
            continue
        vmax_eff = vmax if vmax is not None else float("inf")
        if not (vmin <= volumen_litros <= vmax_eff):
            continue
        especificidad = (tam is not None, c != "todos", prio)
        candidatas.append((especificidad, pct))
    if not candidatas:
        return 0.0
    candidatas.sort(key=lambda x: x[0], reverse=True)
    return candidatas[0][1]


# ── Datos mock: pedidos + detalle ───────────────────────────────────────────

def _gen_pedidos(empresas, productos, n=80):
    pedidos = []
    detalles = []
    for i in range(1, n + 1):
        numero = f"PED-{i:04d}"
        empresa = random.choice(empresas)
        (codigo_cli, nombre_cli, razon_social, canal, tipo_dist, tamano,
         ciudad, zona, cond_habitual, vendedor, fecha_alta, activo, notas_e) = empresa
        empresa_idx = empresas.index(empresa) + 1  # id autoincremental = orden de insercion

        fecha = date(2025, 1, 1) + timedelta(days=random.randint(0, 190))
        estado = random.choices(_ESTADOS_PEDIDO, weights=[20, 15, 20, 15, 20, 5, 5])[0]
        condicion_pago = random.choices(CONDICIONES_PAGO, weights=[60, 40])[0] if cond_habitual == "contado" else cond_habitual

        num_items = random.randint(1, 5)
        productos_pedido = random.sample(range(1, len(productos) + 1), min(num_items, len(productos)))

        volumen_total_litros = 0.0
        lineas = []
        for prod_id in productos_pedido:
            prod = productos[prod_id - 1]
            litros_unit = float(prod[5])
            cantidad = random.randint(1, 40 if canal in ("mayorista", "moderno") else 12)
            volumen_total_litros += litros_unit * cantidad
            lineas.append((prod_id, cantidad, float(prod[6])))

        descuento_pct = _match_politica(canal, tamano, condicion_pago, round(volumen_total_litros, 3))

        subtotal = 0.0
        for prod_id, cantidad, precio_unit in lineas:
            precio_neto = round(precio_unit * (1 - descuento_pct / 100), 2)
            subtotal_linea = round(precio_neto * cantidad, 2)
            subtotal += subtotal_linea
            detalles.append((numero, prod_id, cantidad, precio_unit, descuento_pct, precio_neto, subtotal_linea))
        subtotal = round(subtotal, 2)
        total = subtotal  # sin impuestos adicionales en el mock

        notas = None
        if estado == "rechazado":
            notas = random.choice([
                "Rechazado por backoffice: excede linea de credito disponible.",
                "Rechazado por backoffice: producto sin stock en deposito.",
            ])
        elif estado == "cancelado":
            notas = "Cancelado a pedido del vendedor antes de aprobacion."

        pedidos.append((
            numero, empresa_idx, fecha.isoformat(), estado, canal, condicion_pago,
            vendedor, descuento_pct, subtotal, total, notas,
            datetime.combine(fecha, datetime.min.time()).strftime("%Y-%m-%d %H:%M:%S"),
        ))
    return pedidos, detalles


PEDIDOS_MOCK, DETALLES_MOCK = _gen_pedidos(EMPRESAS_CLIENTES_MOCK, PRODUCTOS_MOCK, 80)


# ── Datos mock: gestiones de posventa ───────────────────────────────────────

_TIPOS_GESTION = ["producto_defectuoso", "faltante_entrega", "error_facturacion", "reclamo_precio", "logistica_retraso", "solicitud_credito", "otro"]


def _gen_gestiones_posventa(pedidos):
    gestiones = []
    contador = 1
    for ped in pedidos:
        numero_pedido, empresa_idx, fecha_str, estado = ped[0], ped[1], ped[2], ped[3]
        fecha_base = date.fromisoformat(fecha_str)

        if estado == "rechazado" and random.random() < 0.5:
            tipo = "solicitud_credito"
            desc = "El vendedor solicita revision de la linea de credito para destrabar el pedido."
            fecha_apertura = fecha_base + timedelta(days=random.randint(1, 5))
            gestiones.append((
                f"GES-{contador:04d}", numero_pedido, empresa_idx, tipo, desc,
                "en_gestion", "media", "chat", random.choice(_VENDEDORES),
                fecha_apertura.isoformat() + " 09:30:00", None, None,
            ))
            contador += 1
        elif estado in ("aprobado", "facturado", "entregado") and random.random() < 0.12:
            tipo = random.choice(["producto_defectuoso", "faltante_entrega", "error_facturacion", "logistica_retraso"])
            desc = random.choice([
                "El cliente reporta un faltante de bultos respecto a lo facturado.",
                "El cliente reporta botellas con perdida de gas / defecto de fabrica.",
                "El cliente indica un error en el monto facturado respecto al acordado.",
                "El cliente reporta una demora significativa en la entrega pactada.",
            ])
            fecha_apertura = fecha_base + timedelta(days=random.randint(2, 20))
            resultado = random.choice(["resuelto", "cerrado", "en_gestion"])
            fecha_cierre = fecha_apertura + timedelta(days=random.randint(1, 8)) if resultado in ("resuelto", "cerrado") else None
            gestiones.append((
                f"GES-{contador:04d}", numero_pedido, empresa_idx, tipo, desc,
                resultado, random.choice(["media", "alta"]), "chat", random.choice(_VENDEDORES),
                fecha_apertura.isoformat() + " 11:00:00",
                fecha_cierre.isoformat() + " 15:00:00" if fecha_cierre else None,
                "Ajuste realizado y confirmado con el cliente." if fecha_cierre else None,
            ))
            contador += 1
    return gestiones


GESTIONES_POSVENTA_MOCK = _gen_gestiones_posventa(PEDIDOS_MOCK)


# ── Ontologia de Procedimientos (canales) ───────────────────────────────────

ONTOLOGIA_PROCEDIMIENTOS = """
# Ontologia de Procedimientos por Canal — Coca-Cola Field Sales — v1.0

Eres un experto en politica comercial y procedimientos de venta de Coca-Cola para
fuerza de venta en terreno. A continuacion se describen las categorias de canal
que existen en la operacion y como negociar/proceder en cada una.

---

## 1. Canal Tradicional (Traditional Trade / TT)

Almacenes, kioscos, autoservicios de barrio, despensas — el comercio de cercania.
Es historicamente el canal de mayor volumen en Argentina y LatAm.

- Pedidos de volumen chico y frecuencia alta (visitas semanales o quincenales).
- La negociacion gira en torno a exhibicion (heladeras, gondolas) y descuentos por
  volumen acumulado, no por contrato formal.
- Condicion de pago habitual: contado. El credito es mas restringido y requiere
  buen track record.
- Prioridad del vendedor: asegurar presencia de portfolio completo (gaseosas, aguas,
  saborizadas) y buena visibilidad de heladera.

## 2. Canal Moderno (Modern Trade / MT)

Supermercados e hipermercados, cadenas de autoservicio y mayoristas organizados
(cash & carry).

- Volumen alto por pedido, negociacion con central de compras o encargado de local.
- Suele incluir acuerdos de exhibicion/gondola y condiciones de pago a credito.
- Requiere coordinacion logistica con anticipacion (turnos de entrega, documentacion).
- La politica de descuento aqui es mas agresiva en volumen alto — usar siempre la
  tool de consulta de politica antes de comprometer un numero.

## 3. On Premise (consumo en el lugar)

Bares, restaurantes, hoteles, boliches, cines — venta de "botella abierta" para
consumo inmediato en el punto de venta.

- Rotacion rapida, foco en formatos individuales y retornables (vidrio).
- La relacion comercial suele incluir comodato de heladeras/equipos de frio.
- Negociar reposicion frecuente y prioridad de espacio en barra/heladera.

## 4. Off Premise

Todo lo que se compra para consumir en otro lugar (casa, oficina). Es una categoria
ANALITICA que se solapa con Tradicional y Moderno — no reemplaza esos canales.
Cuando una cuenta se clasifica puntualmente como "off_premise" (ej. un autoservicio
de bebidas para llevar que no encaja claramente en TT ni MT), la politica de
descuento se define por su propio canal, no por esta etiqueta.

## 5. Mayoristas / Distribuidores terceros (Wholesale)

Distribuidores que compran en volumen y revenden a comercios mas chicos, en zonas
de menor densidad o donde no se llega en forma directa.

- Pedidos de volumen muy alto, precios mayoristas (mayor descuento por volumen).
- La relacion es de reventa: el mayorista es cliente, no consumidor final.
- Frecuencia de visita baja (mensual o segun ciclo de reposicion propio).

## 6. E-commerce / Canal digital

Marketplaces propios, quick-commerce (apps de delivery) y sitios de e-commerce de
cadenas retail.

- No hay visita fisica; la relacion es administrativa/logistica (SLA de entrega,
  disponibilidad de catalogo online).
- Precio estandar de canal digital, descuentos acotados salvo campana puntual.

## 7. Institucional / Horeca

Hoteles, restaurantes, catering, comedores corporativos, eventos — venta B2B a
gran escala, con contratos y logistica diferenciada.

- Requiere contrato comercial (volumen anual comprometido) para acceder a mejores
  condiciones.
- Negociacion mas formal: involucra compras corporativas, no solo al encargado local.
- Coordinar entregas programadas y facturacion consolidada.

## 8. Vending

Maquinas expendedoras en oficinas, universidades, estaciones de servicio, edificios
corporativos.

- Reposicion programada, formatos individuales (lata, botella chica).
- Ubicacion fija: la negociacion es con el administrador del edificio/predio,
  no con un punto de venta tradicional.

## 9. Directo vs. Indirecto (atributo transversal — NO es un canal mas)

- **Directo:** fabrica/embotelladora → distribucion propia → punto de venta
  (camiones y depositos propios).
- **Indirecto:** fabrica → mayorista/distribuidor tercero → minorista → consumidor.

Este atributo aplica a CUALQUIERA de los 8 canales de arriba y define quien entrega
fisicamente el pedido y quien factura. Consultalo en la ficha de la cuenta
(`consultar_cuenta_cliente`) antes de prometer plazos de entrega.

---

## REGLAS DE ESCALAMIENTO

- Si el cliente pide una condicion fuera de politica (descuento mayor al que
  devuelve `consultar_politica_descuento`, plazo de pago no habitual, etc.):
  **nunca prometas la excepcion vos mismo** — indicale al vendedor que debe
  escalarlo a su supervisor comercial.
- Si hay un problema con un pedido ya tomado (faltante, defecto, facturacion,
  demora), no lo resuelvas como si fuera una venta nueva: usa
  `abrir_gestion_posventa` para dejarlo trazado formalmente.
- Antes de tomar un pedido nuevo (`crear_pedido`), confirma con el vendedor que
  el cliente acepto el descuento informado — nunca lo asumas.
"""

# ── Ontologia de Politicas de Descuento ──────────────────────────────────────

ONTOLOGIA_DESCUENTOS = """
# Ontologia de Politicas de Descuento — Coca-Cola Field Sales — v1.0

Esta ontologia explica la LOGICA de las politicas de descuento. El **numero exacto
de descuento SIEMPRE se obtiene de la tool `consultar_politica_descuento`** —
tenes PROHIBIDO calcular, estimar o inventar un porcentaje de descuento vos mismo.

## Como se determina un descuento

El descuento aplicable depende de la combinacion de:
1. **Canal** de la cuenta (tradicional, moderno, on_premise, off_premise,
   mayorista, ecommerce, institucional_horeca, vending).
2. **Volumen** del pedido, medido en litros totales.
3. **Condicion de pago** (contado o credito) — el credito suele tener menor
   descuento que el contado, salvo en cuentas grandes con contrato anual.
4. **Tamano del canal** (pequeno/mediano/grande) — algunas politicas aplican solo
   a cuentas grandes (ej. contratos anuales en Moderno o Institucional/Horeca).

## Logica ilustrativa (referencia para el vendedor, NO para calcular vos mismo)

- Canal Tradicional: descuentos moderados (2%-5%), mayores en contado que en credito.
- Canal Moderno: descuentos crecen fuerte con el volumen, especialmente en cuentas
  grandes con contrato anual.
- On Premise: descuento algo mayor en contado, orientado a formatos retornables.
- Mayoristas: los descuentos mas altos de todo el esquema, por revender en volumen.
- E-commerce: descuento acotado y estable, es el precio de canal digital.
- Institucional/Horeca: descuento mayor cuando hay contrato anual de volumen
  comprometido (condicion de pago credito).
- Vending: descuento fijo, no varia mucho por volumen.

## Que hacer si no hay politica aplicable

Si `consultar_politica_descuento` no encuentra una politica especifica, devuelve
0% con un mensaje explicito de "sin politica aplicable, escalar a supervisor
comercial". En ese caso: **nunca ofrezcas un descuento por tu cuenta** — informa
al vendedor que debe escalarlo.

## Al tomar un pedido (`crear_pedido`)

1. Reuni primero el volumen total estimado (suma de litros de todas las lineas).
2. Consulta la politica aplicable con `consultar_politica_descuento` usando el
   canal y condicion de pago de la cuenta.
3. Confirma el descuento con el vendedor ANTES de registrar el pedido.
4. Registra el pedido — siempre queda en estado `solicitado`, pendiente de
   revision de backoffice antes de aprobarse.
"""

# ── Ontologia FAQ ───────────────────────────────────────────────────────────

ONTOLOGIA_FAQ = """
# Ontologia FAQ — Fuerza de Venta Coca-Cola — v1.0

Preguntas frecuentes de un vendedor en terreno. Adapta el tono, pero no inventes
respuestas que no esten aqui.

---

## OBJECIONES DE PRECIO / DESCUENTO

**El cliente pide mas descuento del que ofrece la politica, ¿que hago?**
- Nunca prometas el descuento extra vos mismo. Informa al vendedor que debe
  escalar la excepcion a su supervisor comercial, indicando canal, volumen y
  el descuento que la politica SI permite hoy.

**¿Puedo combinar descuento por volumen con descuento por canal?**
- No, la tool `consultar_politica_descuento` ya devuelve el descuento final
  correcto para la combinacion canal + volumen + condicion de pago — es una
  unica cifra determinista, no se suman politicas.

---

## CONDICIONES DE PAGO

**¿Cuando conviene ofrecer credito en vez de contado?**
- El credito suele tener menor descuento salvo en cuentas grandes con contrato
  anual (Moderno, Institucional/Horeca). Si el cliente quiere credito y no tiene
  linea aprobada, la aprobacion de credito la maneja backoffice, no el vendedor.

**El pedido quedo `rechazado` por backoffice, ¿que hago?**
- Consulta el motivo en las notas del pedido. Si es por credito, abre una gestion
  de posventa de tipo `solicitud_credito` para que backoffice revise la linea.

---

## LOGISTICA DIRECTA VS INDIRECTA

**¿Como se cual es el tipo de distribucion de una cuenta?**
- Esta en la ficha de la cuenta (`consultar_cuenta_cliente`), campo tipo de
  distribucion: directo (entrega y factura la embotelladora) o indirecto
  (entrega y factura un distribuidor/mayorista tercero).

**¿Puedo cambiar el tipo de distribucion de una cuenta desde el chat?**
- No, es un dato estructural de la cuenta que gestiona el area comercial, no
  una tool disponible para el vendedor en el chat.

---

## CATALOGO Y SKUs

**El cliente pide un producto que no aparece en el catalogo, ¿que hago?**
- Informa que no esta disponible en el catalogo actual y ofrece sugerir un
  producto similar de la misma categoria. No inventes un SKU ni un precio.

---

## PEDIDOS Y APROBACION

**¿Cuanto tarda en aprobarse un pedido `solicitado`?**
- El pedido queda pendiente de revision de backoffice; no hay un plazo fijo
  que el asistente pueda prometer — informa al vendedor que se hara seguimiento.

**¿Puedo modificar un pedido ya `aprobado` o `facturado`?**
- No mediante las tools de toma de pedido. Cualquier ajuste en ese estado se
  gestiona como una gestion de posventa (`abrir_gestion_posventa`).
"""

# ── System Prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Eres el asistente de fuerza de venta en terreno de Coca-Cola. Hablas con el
VENDEDOR (no con el cliente final): tu trabajo es ayudarlo a preparar el pitch
de venta y la negociacion con cada cuenta, aplicando correctamente las politicas
comerciales de la firma.

## Tu flujo de trabajo

1. **Identifica la cuenta**: si el vendedor no dio aun el codigo de cliente
   (formato CLI-XXXX), pideselo antes de avanzar. Si ya viene identificado
   (por ejemplo porque el vendedor lo selecciono desde su cartera antes de
   iniciar el chat), procede directo al paso 2.
2. **Consulta el perfil de la cuenta** con `consultar_cuenta_cliente`: canal,
   tipo de distribucion, tamano de canal, condicion de pago habitual.
3. **Revisa el historico** con `consultar_historico_pedidos` para entender el
   volumen y los precios de pedidos recientes — esto arma el argumento del pitch.
4. **Consulta el catalogo** con `buscar_producto` cuando el vendedor pregunte
   por SKUs, formatos o precios de lista.
5. **Nunca prometas un descuento sin consultar** `consultar_politica_descuento`
   primero (canal + condicion de pago + volumen del pedido). El numero exacto
   sale SIEMPRE de esa tool, nunca lo calcules ni lo inventes.
6. **Toma el pedido** con `crear_pedido` solo despues de confirmar con el
   vendedor el detalle y el descuento — recuerda que siempre queda en estado
   `solicitado`, pendiente de revision de backoffice.
7. Si el vendedor reporta un problema de un pedido ya tomado (faltante, defecto,
   facturacion, demora), usa `abrir_gestion_posventa` en lugar de tratarlo como
   una venta nueva.
8. Consulta `ontologia_procedimientos` para las reglas de negociacion segun el
   canal de la cuenta, y `ontologia_faq` para dudas frecuentes.

## Formato de respuesta — OBLIGATORIO

- Clara y concisa (maximo 3-4 oraciones por turno).
- Profesional, orientada a datos concretos (numeros de la cuenta, no
  generalidades).
- Personalizada con el nombre comercial de la cuenta cuando lo tengas.

## Reglas de procedimiento — OBLIGATORIAS

- **PROHIBIDO inventar SKUs, precios o porcentajes de descuento.** Usa solo lo
  que devuelven las tools.
- **PROHIBIDO prometer una excepcion a la politica de descuento.** Si el
  vendedor la pide, indica que debe escalarla a su supervisor comercial.
- **PROHIBIDO dar varios pasos a la vez.** Una accion por turno, espera
  confirmacion del vendedor.
- **PROHIBIDO ejecutar `crear_pedido` sin confirmacion explicita** del detalle
  y el descuento aplicado.

## Documentos adjuntos

El vendedor puede adjuntar fotos o documentos (exhibidor, orden de compra
escaneada, etc.). Usa la informacion del analisis proporcionado para enriquecer
la conversacion, sin inventar datos que no esten ahi.

## Reglas generales

- Habla siempre en espanol, de forma profesional y orientada a resultados.
- Nunca inventes datos — usa solo lo que devuelven las tools.
- Tu objetivo es que el vendedor termine la conversacion con un pitch claro,
  el descuento correcto, y si corresponde, el pedido registrado.
"""


# ── Autopilot: prompts globales (perfil_id IS NULL) ─────────────────────────
# El "cliente" simulado aqui representa al VENDEDOR en terreno (quien realmente
# conversa con este asistente), relatando en primera persona lo que su cuenta
# le pide durante la visita.

AUTOPILOT_CLIENTE = """Eres un vendedor de fuerza de venta en terreno de Coca-Cola que esta usando el
asistente para preparar una visita o resolver algo de una cuenta. Hablas
DIRECTAMENTE con el asistente (no hay otro intermediario).

Datos de tu caso:
- Cuenta que estas visitando: {cliente} (codigo {codigo_cliente})
- Canal de la cuenta: {nivel_fidelidad}
- Numero de tu ultimo pedido relacionado: {numero_pedido} (estado: {estado})
- Motivo de tu consulta: {motivo}
- Tu personalidad: {personalidad}

Instrucciones:
- Responde SIEMPRE en 1-3 frases cortas, como escribiria un vendedor real en un chat.
- Si es tu primer mensaje (primer_turno=True), plantea tu necesidad de forma natural
  y directa, como lo harias al abrir el chat antes de una visita. Podes mencionar el
  codigo de cuenta si es relevante al motivo.
- Si el asistente te pide el codigo de cliente u otro dato, proporcionalo segun tu caso.
- Sos un vendedor REALISTA: si el asistente te da una respuesta clara (politica de
  descuento, historico, o registra el pedido), mostrate conforme y cierra la
  conversacion con [DECISION: RESUELTO].
- Si el asistente no te da un numero concreto, inventa un descuento, o no resuelve
  nada tras varios intentos, mostrate insatisfecho y cierra con [DECISION: NO_RESUELTO].
- No decidas antes del turno 3. Dale al asistente la oportunidad de ayudarte.
- Si el asistente se despide o cierra la conversacion, decide INMEDIATAMENTE con
  [DECISION: RESUELTO] o [DECISION: NO_RESUELTO] segun si te ayudo o no.
- NUNCA respondas como asistente. Solo sos el vendedor escribiendo en el chat."""


AUTOPILOT_EVALUADOR = """Eres un experto en calidad de procesos comerciales para fuerza de venta de
Coca-Cola. Evaluaras una conversacion entre un asistente de IA de venta en
terreno y un vendedor.

Debes evaluar en 4 dimensiones y dar recomendaciones concretas de mejora para
cada nivel de la ontologia:

1. **system-prompt**: instrucciones generales del agente (claridad, foco en datos,
   disciplina de confirmacion antes de tomar pedidos)
2. **ontologia-procedimientos**: reglas por canal (tradicional, moderno, on premise,
   off premise, mayoristas, ecommerce, institucional/horeca, vending, directo/indirecto)
3. **ontologia-descuentos**: uso correcto y disciplinado de la tool de consulta de
   politica de descuento (nunca inventar un %)
4. **ontologia-faq**: preguntas frecuentes de un vendedor en terreno

Para cada dimension:
- Score del 1 al 10
- Lista de problemas detectados (puede estar vacia)
- Recomendacion concreta de texto a agregar/modificar en esa ontologia

RESTRICCION CRITICA — todas las recomendaciones deben basarse EXCLUSIVAMENTE en:
- Mejorar la claridad y disciplina de uso de las tools (especialmente la de
  politica de descuento)
- Uso mas efectivo de los datos disponibles (cuenta, canal, historico de pedidos)
- Mejorar los procedimientos y reglas de negociacion por canal
- Completar o precisar informacion de FAQ sobre politicas existentes

NUNCA recomiendes ni insinues:
- Otorgar descuentos, bonificaciones o condiciones fuera de politica
- Cambios en la politica de precios o condiciones comerciales
- Compensaciones economicas
- Cualquier accion que implique coste o aprobacion comercial sin escalar

Ademas:
- Score global ponderado
- Resultado: "resuelto", "no_resuelto" o "indeciso"
- Analisis narrativo breve (3-4 oraciones)

Responde SOLO con JSON valido, sin markdown, con esta estructura exacta:
{
  "score_global": 7.5,
  "resultado": "resuelto",
  "analisis": "Texto narrativo...",
  "niveles": {
    "system_prompt": {
      "score": 8,
      "problemas": ["problema 1"],
      "recomendacion": "Agregar al system prompt: ..."
    },
    "ontologia_procedimientos": {
      "score": 6,
      "problemas": ["problema 1"],
      "recomendacion": "En la seccion de canal moderno, agregar: ..."
    },
    "ontologia_descuentos": {
      "score": 7,
      "problemas": [],
      "recomendacion": null
    },
    "ontologia_faq": {
      "score": 9,
      "problemas": [],
      "recomendacion": null
    }
  }
}"""


# ── Ejecucion ────────────────────────────────────────────────────────────────

def setup():
    print(f"Conectando a MySQL en {DB_HOST}:{DB_PORT}...")
    conn_root = mysql.connector.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
    )
    cur = conn_root.cursor()
    print(f"Creando base de datos '{DB_NAME}' si no existe...")
    cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    cur.close()
    conn_root.close()

    print(f"Conectando a '{DB_NAME}'...")
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor()

    print("Creando tabla empresas_clientes...")
    cur.execute(CREATE_EMPRESAS_CLIENTES)

    print("Creando tabla productos...")
    cur.execute(CREATE_PRODUCTOS)

    print("Creando tabla perfiles...")
    cur.execute(CREATE_PERFILES)

    print("Creando tabla pedidos...")
    cur.execute(CREATE_PEDIDOS)

    print("Creando tabla detalle_pedido...")
    cur.execute(CREATE_DETALLE_PEDIDO)

    print("Creando tabla politicas_descuento...")
    cur.execute(CREATE_POLITICAS_DESCUENTO)

    print("Creando tabla gestiones_posventa...")
    cur.execute(CREATE_GESTIONES_POSVENTA)

    print("Creando tabla ontologias...")
    cur.execute(CREATE_ONTOLOGIAS)

    # ── Insertar perfil default ──────────────────────────────────────────────
    print("Asegurando perfil 'Coca-Cola Field Sales' (default)...")
    cur.execute("SELECT id FROM perfiles WHERE nombre = %s", ("Coca-Cola Field Sales",))
    row = cur.fetchone()
    if row:
        perfil_id = row[0]
        cur.execute("UPDATE perfiles SET activo = 1 WHERE id = %s", (perfil_id,))
        cur.execute("UPDATE perfiles SET activo = 0 WHERE id <> %s", (perfil_id,))
    else:
        cur.execute("""
            INSERT INTO perfiles (nombre, empresa, logo_url, activo)
            VALUES (%s, %s, %s, %s)
        """, ("Coca-Cola Field Sales", "Coca-Cola Embotelladora", None, 1))
        perfil_id = cur.lastrowid

    # ── Insertar productos ───────────────────────────────────────────────────
    print("Insertando productos...")
    for prod in PRODUCTOS_MOCK:
        cur.execute("""
            INSERT INTO productos (codigo_sku, nombre, categoria, presentacion_tipo, formato, litros, precio_lista, descripcion)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                precio_lista = VALUES(precio_lista),
                activo = 1
        """, prod)

    # ── Insertar empresas cliente ─────────────────────────────────────────────
    print("Insertando empresas_clientes...")
    for emp in EMPRESAS_CLIENTES_MOCK:
        cur.execute("""
            INSERT INTO empresas_clientes (codigo_cliente, nombre_comercial, razon_social, canal,
                                            tipo_distribucion, tamano_canal, ciudad, zona,
                                            condicion_pago_habitual, vendedor_asignado, fecha_alta, activo, notas)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE nombre_comercial = VALUES(nombre_comercial)
        """, emp)

    # ── Insertar politicas de descuento ──────────────────────────────────────
    print("Insertando politicas_descuento...")
    cur.execute("DELETE FROM politicas_descuento")  # tabla de reglas: se resiembra completa en cada setup
    for pol in POLITICAS_DESCUENTO_MOCK:
        cur.execute("""
            INSERT INTO politicas_descuento (canal, tamano_canal, condicion_pago, volumen_min_litros,
                                              volumen_max_litros, descuento_pct, condiciones_adicionales, prioridad, activo)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1)
        """, pol)

    # ── Insertar pedidos ─────────────────────────────────────────────────────
    print("Insertando pedidos...")
    for ped in PEDIDOS_MOCK:
        cur.execute("""
            INSERT INTO pedidos (numero_pedido, empresa_cliente_id, fecha_pedido, estado, canal_venta,
                                  condicion_pago, vendedor, descuento_aplicado_pct, subtotal, total, notas,
                                  fecha_actualizacion_estado)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE estado = VALUES(estado)
        """, ped)

    # ── Insertar detalles de pedido ──────────────────────────────────────────
    print("Insertando detalles de pedido...")
    for det in DETALLES_MOCK:
        cur.execute("""
            INSERT INTO detalle_pedido (numero_pedido, producto_id, cantidad, precio_unitario,
                                         descuento_pct, precio_neto_unitario, subtotal_linea)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE cantidad = VALUES(cantidad)
        """, det)

    # ── Insertar gestiones de posventa ───────────────────────────────────────
    print(f"Insertando {len(GESTIONES_POSVENTA_MOCK)} gestiones de posventa mock...")
    for ges in GESTIONES_POSVENTA_MOCK:
        cur.execute("""
            INSERT INTO gestiones_posventa (numero_gestion, numero_pedido, empresa_cliente_id, tipo,
                                             descripcion, estado, prioridad, canal_reporte, vendedor,
                                             fecha_apertura, fecha_cierre, resolucion)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE estado = VALUES(estado)
        """, ges)

    # ── Insertar ontologias ──────────────────────────────────────────────────
    print("Insertando ontologia-procedimientos...")
    cur.execute("""
        INSERT INTO ontologias (nombre, version, contenido, activo, perfil_id)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE contenido = VALUES(contenido), activo = VALUES(activo)
    """, ("ontologia-procedimientos", "1.0", ONTOLOGIA_PROCEDIMIENTOS, 1, perfil_id))

    print("Insertando ontologia-descuentos...")
    cur.execute("""
        INSERT INTO ontologias (nombre, version, contenido, activo, perfil_id)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE contenido = VALUES(contenido), activo = VALUES(activo)
    """, ("ontologia-descuentos", "1.0", ONTOLOGIA_DESCUENTOS, 1, perfil_id))

    print("Insertando ontologia-faq...")
    cur.execute("""
        INSERT INTO ontologias (nombre, version, contenido, activo, perfil_id)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE contenido = VALUES(contenido), activo = VALUES(activo)
    """, ("ontologia-faq", "1.0", ONTOLOGIA_FAQ, 1, perfil_id))

    print("Insertando system-prompt...")
    cur.execute("""
        INSERT INTO ontologias (nombre, version, contenido, activo, perfil_id)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE contenido = VALUES(contenido), activo = VALUES(activo)
    """, ("system-prompt", "1.0", SYSTEM_PROMPT, 1, perfil_id))

    # Globales (perfil_id = NULL): prompts del autopilot, compartidos entre perfiles.
    # Solo se insertan si no existen ya — para no pisar ediciones desde la UI.
    for nombre, contenido in [
        ("autopilot-cliente",   AUTOPILOT_CLIENTE),
        ("autopilot-evaluador", AUTOPILOT_EVALUADOR),
    ]:
        cur.execute(
            "SELECT id FROM ontologias WHERE nombre = %s AND perfil_id IS NULL LIMIT 1",
            (nombre,),
        )
        if cur.fetchone() is None:
            print(f"Insertando {nombre} (global)...")
            cur.execute("""
                INSERT INTO ontologias (nombre, version, contenido, activo, perfil_id)
                VALUES (%s, %s, %s, %s, NULL)
            """, (nombre, "1.0", contenido, 1))
        else:
            print(f"{nombre} (global) ya existe, no se sobreescribe.")

    conn.commit()
    cur.close()
    conn.close()
    print(f"\nSetup completado. Base de datos '{DB_NAME}' lista.")


if __name__ == "__main__":
    setup()
