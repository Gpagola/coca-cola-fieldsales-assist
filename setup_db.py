"""
Script para crear la BBDD Distribuidora Pampa Field Sales Assist y cargar datos mock B2B.
Ejecutar una sola vez: python setup_db.py

IMPORTANTE: este script crea/puebla el schema definido en DB_NAME (.env).
Debe apuntar a un schema NUEVO y separado (ej. 'pampa_fieldsales'), nunca
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
DB_NAME     = os.getenv("DB_NAME", "pampa_fieldsales")
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
    contexto_cuenta     LONGTEXT,
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

# ── Datos mock: productos (surtido Distribuidora Pampa) ─────────────────────
# presentacion_tipo = "unidad" (venta suelta) o "bulto" (pack/caja de reventa).
# litros = volumen/peso equivalente en kg o L, usado solo para escalonar el
# descuento por volumen del pedido (el motor de descuento suma este campo).

PRODUCTOS_MOCK = [
    # codigo_sku, nombre, categoria, presentacion_tipo, formato, kg/L, precio_lista, descripcion
    # ── Almacen: aceites, secos, conservas ──────────────────────────────────
    ("ACE-PAM-900",    "Aceite de Girasol Pampa",       "almacen", "unidad", "botella 900ml",     0.900, 1450.00, "Aceite de girasol, botella individual."),
    ("ACE-PAM-900-CJ", "Aceite de Girasol Pampa",       "almacen", "bulto",  "caja x12 900ml",   10.800, 16200.00,"Aceite de girasol, caja de 12 unidades para reventa."),
    ("ACE-SOL-1500",   "Aceite de Girasol Solcampo",    "almacen", "unidad", "botella 1.5L",      1.500, 2350.00, "Aceite de girasol linea premium Solcampo."),
    ("ARR-PAM-1000",   "Arroz Largo Fino Pampa",        "almacen", "unidad", "bolsa 1kg",         1.000, 1200.00, "Arroz largo fino, bolsa de 1 kilo."),
    ("ARR-PAM-1000-BU","Arroz Largo Fino Pampa",        "almacen", "bulto",  "bulto x10 1kg",    10.000, 11000.00,"Arroz largo fino, bulto de 10 kilos para reventa."),
    ("FID-PAM-500",    "Fideos Guiseros Pampa",         "almacen", "unidad", "paquete 500g",      0.500, 780.00,  "Fideos secos tipo guisero, paquete de 500g."),
    ("FID-PAM-500-BU", "Fideos Guiseros Pampa",         "almacen", "bulto",  "bulto x20 500g",   10.000, 14800.00,"Fideos guiseros, bulto de 20 paquetes."),
    ("FID-DR-500",     "Fideos Tallarin Dona Rosa",     "almacen", "unidad", "paquete 500g",      0.500, 850.00,  "Fideos tallarin al huevo linea Dona Rosa."),
    ("HAR-PAM-1000",   "Harina 0000 Pampa",             "almacen", "unidad", "bolsa 1kg",         1.000, 950.00,  "Harina de trigo 0000, bolsa de 1 kilo."),
    ("HAR-PAM-1000-BU","Harina 0000 Pampa",             "almacen", "bulto",  "bulto x10 1kg",    10.000, 8800.00, "Harina 0000, bulto de 10 kilos para reventa."),
    ("AZU-PAM-1000",   "Azucar Pampa",                  "almacen", "unidad", "bolsa 1kg",         1.000, 1100.00, "Azucar comun tipo A, bolsa de 1 kilo."),
    ("AZU-PAM-1000-BU","Azucar Pampa",                  "almacen", "bulto",  "bulto x10 1kg",    10.000, 10200.00,"Azucar comun, bulto de 10 kilos."),
    ("YER-MC-1000",    "Yerba Mate Monte Claro",        "almacen", "unidad", "paquete 1kg",       1.000, 2600.00, "Yerba mate con palo linea Monte Claro, 1 kilo."),
    ("YER-MC-1000-BU", "Yerba Mate Monte Claro",        "almacen", "bulto",  "bulto x10 1kg",    10.000, 24500.00,"Yerba mate Monte Claro, bulto de 10 kilos."),
    ("YER-PAM-500",    "Yerba Mate Pampa",              "almacen", "unidad", "paquete 500g",      0.500, 1450.00, "Yerba mate con palo, paquete de 500g."),
    ("CAF-PAM-500",    "Cafe Molido Pampa",             "almacen", "unidad", "paquete 500g",      0.500, 3200.00, "Cafe molido tostado, paquete de 500g."),
    ("TOM-PAM-520",    "Pure de Tomate Pampa",          "almacen", "unidad", "tetra 520g",        0.520, 780.00,  "Pure de tomate, envase tetra de 520g."),
    ("TOM-PAM-520-CJ", "Pure de Tomate Pampa",          "almacen", "bulto",  "caja x12 520g",     6.240, 8600.00, "Pure de tomate, caja de 12 unidades."),
    ("ARV-PAM-300",    "Arvejas Pampa",                 "almacen", "unidad", "lata 300g",         0.300, 620.00,  "Arvejas remojadas en lata de 300g."),
    ("ARV-PAM-300-CJ", "Arvejas Pampa",                 "almacen", "bulto",  "caja x24 300g",     7.200, 13800.00,"Arvejas Pampa, caja de 24 latas."),
    ("ATU-PAM-170",    "Atun Pampa al Natural",         "almacen", "unidad", "lata 170g",         0.170, 1350.00, "Atun al natural en lata de 170g."),
    ("ATU-PAM-170-CJ", "Atun Pampa al Natural",         "almacen", "bulto",  "caja x24 170g",     4.080, 30500.00,"Atun al natural, caja de 24 latas."),
    ("LEN-PAM-400",    "Lentejas Pampa",                "almacen", "unidad", "paquete 400g",      0.400, 890.00,  "Lentejas secas, paquete de 400g."),
    ("SAL-PAM-500",    "Sal Fina Pampa",                "almacen", "unidad", "paquete 500g",      0.500, 420.00,  "Sal fina de mesa, paquete de 500g."),
    # ── Galletitas y snacks ─────────────────────────────────────────────────
    ("GAL-DR-300",     "Galletitas Dulces Dona Rosa",   "galletitas_snacks", "unidad", "paquete 300g", 0.300, 720.00,  "Galletitas dulces surtidas linea Dona Rosa."),
    ("GAL-DR-300-CJ",  "Galletitas Dulces Dona Rosa",   "galletitas_snacks", "bulto",  "caja x12 300g", 3.600, 8000.00, "Galletitas dulces Dona Rosa, caja de 12."),
    ("GAL-PAM-200",    "Galletitas Crackers Pampa",     "galletitas_snacks", "unidad", "paquete 200g", 0.200, 560.00,  "Galletitas crackers saladas, paquete de 200g."),
    ("GAL-PAM-200-CJ", "Galletitas Crackers Pampa",     "galletitas_snacks", "bulto",  "caja x12 200g", 2.400, 6300.00, "Galletitas crackers, caja de 12."),
    ("ALF-PAM-U",      "Alfajor Triple Pampa",          "galletitas_snacks", "unidad", "unidad 70g",   0.070, 480.00,  "Alfajor triple relleno de dulce de leche."),
    ("ALF-PAM-CJ",     "Alfajor Triple Pampa",          "galletitas_snacks", "bulto",  "caja x24",     1.680, 10500.00,"Alfajor triple, caja de 24 unidades."),
    ("SNK-CRO-120",    "Papas Fritas Crocantes",        "galletitas_snacks", "unidad", "paquete 120g", 0.120, 850.00,  "Papas fritas clasicas, paquete de 120g."),
    ("SNK-CRO-120-BU", "Papas Fritas Crocantes",        "galletitas_snacks", "bulto",  "bulto x10 120g",1.200, 7900.00, "Papas fritas Crocantes, bulto de 10."),
    # ── Bebidas (gaseosas, aguas, jugos) ────────────────────────────────────
    ("GAS-PAM-COLA-2250",   "Gaseosa Cola Pampa",       "bebidas", "unidad", "botella 2.25L",    2.250, 1350.00, "Gaseosa sabor cola, botella familiar de 2.25L."),
    ("GAS-PAM-COLA-2250-PK","Gaseosa Cola Pampa",       "bebidas", "bulto",  "pack x6 2.25L",   13.500, 7500.00, "Gaseosa cola, pack de 6 botellas familiares."),
    ("GAS-PAM-COLA-500",    "Gaseosa Cola Pampa",       "bebidas", "unidad", "botella 500ml",    0.500, 550.00,  "Gaseosa sabor cola, botella individual."),
    ("GAS-PAM-LIMA-2250",   "Gaseosa Lima-Limon Pampa", "bebidas", "unidad", "botella 2.25L",    2.250, 1300.00, "Gaseosa lima-limon, botella familiar."),
    ("GAS-PAM-NAR-2250",    "Gaseosa Naranja Pampa",    "bebidas", "unidad", "botella 2.25L",    2.250, 1300.00, "Gaseosa sabor naranja, botella familiar."),
    ("AGU-PAM-2000",   "Agua Mineral sin Gas Pampa",    "bebidas", "unidad", "botella 2L",        2.000, 720.00,  "Agua mineral sin gas, botella de 2L."),
    ("AGU-PAM-2000-PK","Agua Mineral sin Gas Pampa",    "bebidas", "bulto",  "pack x6 2L",       12.000, 4000.00, "Agua mineral sin gas, pack de 6 botellas."),
    ("AGU-PAM-500",    "Agua Mineral sin Gas Pampa",    "bebidas", "unidad", "botella 500ml",     0.500, 380.00,  "Agua mineral sin gas, botella individual."),
    ("JUG-PAM-NAR-SOB","Jugo en Polvo Pampa Naranja",   "bebidas", "unidad", "sobre 7g",          0.050, 180.00,  "Jugo en polvo sabor naranja, rinde 2L."),
    ("JUG-PAM-NAR-SOB-BU","Jugo en Polvo Pampa Naranja","bebidas", "bulto",  "bulto x20 sobres",  1.000, 3200.00, "Jugo en polvo naranja, bulto de 20 sobres."),
    ("JUG-PAM-EXP-1000","Jugo Exprimido Pampa Naranja", "bebidas", "unidad", "botella 1L",        1.000, 980.00,  "Jugo exprimido de naranja, botella de 1L."),
    # ── Lacteos ──────────────────────────────────────────────────────────────
    ("LEC-CV-1000",    "Leche Entera Campo Verde",      "lacteos", "unidad", "sachet 1L",         1.000, 850.00,  "Leche entera larga vida, sachet de 1L."),
    ("LEC-CV-1000-CJ", "Leche Entera Campo Verde",      "lacteos", "bulto",  "caja x12 1L",      12.000, 9600.00, "Leche entera, caja de 12 sachets."),
    ("LEC-CV-DESC-1000","Leche Descremada Campo Verde", "lacteos", "unidad", "sachet 1L",         1.000, 870.00,  "Leche descremada larga vida, sachet de 1L."),
    ("YOG-CV-900",     "Yogur Bebible Campo Verde",     "lacteos", "unidad", "botella 900g",      0.900, 1250.00, "Yogur bebible sabor frutilla, botella de 900g."),
    ("QUE-CV-1000",    "Queso Cremoso Campo Verde",     "lacteos", "unidad", "horma 1kg",         1.000, 6800.00, "Queso cremoso, horma de 1 kilo."),
    ("MAN-CV-200",     "Manteca Campo Verde",           "lacteos", "unidad", "pan 200g",          0.200, 1400.00, "Manteca de primera calidad, pan de 200g."),
    ("DDL-PAM-400",    "Dulce de Leche Pampa",          "lacteos", "unidad", "pote 400g",         0.400, 1350.00, "Dulce de leche clasico, pote de 400g."),
    # ── Limpieza ─────────────────────────────────────────────────────────────
    ("LAV-BLA-1000",   "Lavandina Blancol",             "limpieza", "unidad", "botella 1L",       1.000, 620.00,  "Lavandina concentrada, botella de 1L."),
    ("LAV-BLA-1000-CJ","Lavandina Blancol",             "limpieza", "bulto",  "caja x12 1L",     12.000, 6900.00, "Lavandina, caja de 12 botellas."),
    ("LAV-BLA-2000",   "Lavandina Blancol",             "limpieza", "unidad", "botella 2L",       2.000, 1050.00, "Lavandina concentrada, botella de 2L."),
    ("DET-ESP-750",    "Detergente Espuma",             "limpieza", "unidad", "botella 750ml",    0.750, 890.00,  "Detergente lavavajilla concentrado, 750ml."),
    ("DET-ESP-750-CJ", "Detergente Espuma",             "limpieza", "bulto",  "caja x12 750ml",   9.000, 9900.00, "Detergente Espuma, caja de 12."),
    ("JAB-PAM-800",    "Jabon en Polvo Pampa",          "limpieza", "unidad", "paquete 800g",     0.800, 1650.00, "Jabon en polvo para ropa, paquete de 800g."),
    ("JAB-PAM-800-BU", "Jabon en Polvo Pampa",          "limpieza", "bulto",  "bulto x10 800g",   8.000, 15200.00,"Jabon en polvo, bulto de 10 paquetes."),
    ("PAP-PAM-X4",     "Papel Higienico Pampa x4",      "limpieza", "unidad", "pack x4 rollos",   0.400, 950.00,  "Papel higienico doble hoja, pack de 4 rollos."),
    ("PAP-PAM-X4-BU",  "Papel Higienico Pampa x4",      "limpieza", "bulto",  "bulto x10 packs",  4.000, 8800.00, "Papel higienico, bulto de 10 packs de 4."),
    ("ROL-PAM-X2",     "Rollo de Cocina Pampa x2",      "limpieza", "unidad", "pack x2 rollos",   0.300, 780.00,  "Rollo de cocina absorbente, pack de 2."),
    ("JBP-PAM-200",    "Jabon Blanco en Pan Pampa",     "limpieza", "unidad", "pan 200g",         0.200, 340.00,  "Jabon blanco para ropa, pan de 200g."),
    # ── Perfumeria / cuidado personal ───────────────────────────────────────
    ("TOC-FRE-90",     "Jabon de Tocador Frescor",      "perfumeria", "unidad", "pastilla 90g",   0.090, 380.00,  "Jabon de tocador perfumado, pastilla de 90g."),
    ("TOC-FRE-90-CJ",  "Jabon de Tocador Frescor",      "perfumeria", "bulto",  "caja x12 90g",   1.080, 4200.00, "Jabon de tocador Frescor, caja de 12."),
    ("SHA-SED-350",    "Shampoo Seda Natural",          "perfumeria", "unidad", "botella 350ml",  0.350, 1450.00, "Shampoo uso frecuente, botella de 350ml."),
    ("PAS-PAM-90",     "Pasta Dental Pampa",            "perfumeria", "unidad", "pomo 90g",       0.090, 720.00,  "Pasta dental con fluor, pomo de 90g."),
    ("PAS-PAM-90-CJ",  "Pasta Dental Pampa",            "perfumeria", "bulto",  "caja x12 90g",   1.080, 7900.00, "Pasta dental Pampa, caja de 12."),
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
    # canal, tamano_canal, condicion_pago, vol_min_L, vol_max_L, descuento_pct, condiciones_adicionales, prioridad, activo
    ("tradicional", None,      "contado",  0,     50,     3.0,  None,                                             10, 1),
    ("tradicional", None,      "contado",  50,    150,    5.0,  None,                                             10, 1),
    ("tradicional", None,      "contado",  150,   None,   7.0,  "Volumen alto para el canal",                     10, 1),
    ("tradicional", "grande",  "contado",  0,     None,   6.0,  "Cuenta tradicional de mayor escala (ej. autoservicio grande)", 20, 1),
    ("tradicional", None,      "credito",  0,     150,    2.0,  "Sujeto a linea de credito aprobada",            10, 1),
    ("tradicional", None,      "credito",  150,   None,   4.0,  "Volumen alto, sujeto a linea de credito aprobada", 10, 1),
    ("moderno",     None,      "contado",  0,     500,    6.0,  None,                                             10, 1),
    ("moderno",     None,      "contado",  500,   2000,   10.0, "Requiere exhibicion acordada",                   10, 1),
    ("moderno",     "pequeno", "contado",  0,     500,    5.0,  "Cuenta moderna de menor escala",                 20, 1),
    ("moderno",     "mediano", "contado",  500,   2000,   8.0,  "Cuenta moderna de tamano medio",                 20, 1),
    ("moderno",     "grande",  "contado",  2000,  None,   12.0, "Volumen muy alto, cuenta grande",                20, 1),
    ("moderno",     "pequeno", "credito",  0,     1000,   5.0,  "Cuenta moderna de menor escala, credito",        20, 1),
    ("moderno",     "grande",  "credito",  0,     2000,   7.0,  "Contrato comercial anual",                       20, 1),
    ("on_premise",  None,      "contado",  0,     300,    8.0,  "Aplica a insumos de cocina comprados en bulto",  10, 1),
    ("on_premise",  "grande",  "contado",  300,   None,   9.0,  "On premise de alta rotacion (boliche/hotel grande)", 20, 1),
    ("on_premise",  None,      "credito",  0,     300,    5.0,  None,                                             10, 1),
    ("off_premise", None,      "contado",  0,     100,    4.0,  None,                                             10, 1),
    ("off_premise", None,      "contado",  100,   None,   5.5,  "Volumen alto para off premise",                  10, 1),
    ("off_premise", None,      "credito",  0,     None,   2.5,  "Credito para off premise",                       10, 1),
    ("mayorista",   None,      "contado",  1000,  None,   15.0, "Volumen minimo de reventa",                       10, 1),
    ("mayorista",   "grande",  "contado",  3000,  None,   18.0, "Distribuidor de gran escala",                     20, 1),
    ("mayorista",   None,      "credito",  1000,  None,   12.0, "Sujeto a linea de credito aprobada",             10, 1),
    ("ecommerce",   None,      "contado",  0,     None,   3.0,  "Precio online estandar",                         10, 1),
    ("ecommerce",   None,      "credito",  0,     None,   2.0,  "Credito canal digital, poco frecuente",          10, 1),
    ("institucional_horeca", None, "contado", 0,   2000,   10.0, None,                                            10, 1),
    ("institucional_horeca", None, "credito", 2000, None,  14.0, "Requiere contrato anual",                        20, 1),
    ("institucional_horeca", "grande", "credito", 0, None, 15.0, "Cadena hotelera con contrato anual, gran escala", 30, 1),
    ("vending",     None,      "contado",  0,     None,   5.0,  None,                                             10, 1),
    ("vending",     None,      "credito",  0,     None,   3.0,  "Poco frecuente en vending",                       10, 1),
    ("todos",       None,      "contado",  0,     None,   0.0,  "Piso por defecto — sin politica especifica",     -1, 1),
    ("todos",       None,      "credito",  0,     None,   0.0,  "Piso por defecto — sin politica especifica",     -1, 1),
    # Ejemplos de campana estacional — cargadas INACTIVAS por defecto (activo=0):
    # se activan/desactivan desde el editor de Politicas de descuento segun vigencia.
    ("tradicional", None,      "contado",  0,     50,     6.0,  "Promocion de verano (diciembre-marzo)",          30, 0),
    ("moderno",     None,      "contado",  0,     500,    8.0,  "Promocion fin de ano (diciembre)",               30, 0),
]


def _match_politica(canal, tamano, condicion_pago, volumen_litros, politicas=POLITICAS_DESCUENTO_MOCK):
    """Replica en Python la misma logica de seleccion determinista que usara la tool
    consultar_politica_descuento en chatbot.py: filtra por canal/condicion_pago/volumen/activo,
    ordena por especificidad (tamano_canal exacto > canal exacto > prioridad) y toma la primera."""
    candidatas = []
    for c, tam, cond, vmin, vmax, pct, cond_ad, prio, activo in politicas:
        if not activo:
            continue
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
# Ontologia de Procedimientos por Canal — Distribuidora Pampa — v2.0

Eres un experto en politica comercial y procedimientos de venta de Distribuidora
Pampa, una distribuidora mayorista de alimentos, bebidas y limpieza que abastece
al comercio en Argentina. A continuacion se describen las categorias de canal que
existen en la operacion, con sub-casos reales, estandares de ejecucion en punto de
venta, frecuencia de visita y guiones de negociacion. Usa esto para asesorar al
vendedor (preventista) con precision segun el tipo de cuenta que tiene enfrente.

El surtido se organiza en categorias: almacen (aceites, secos, conservas),
galletitas y snacks, bebidas (gaseosas, aguas, jugos), lacteos, limpieza y
perfumeria. La mayoria de los SKUs se vende por unidad o por bulto/caja (para
reventa) — recorda distinguir ambos formatos al armar el pedido.

---

## 1. Canal Tradicional (Traditional Trade / TT)

Almacenes, kioscos, autoservicios de barrio, despensas — el comercio de cercania.
Es historicamente el canal de mayor cantidad de cuentas del pais.

**Sub-casos frecuentes:**
- *Kiosco de esquina (tamano pequeno):* espacio de gondola limitado — prioriza los
  SKUs de mayor rotacion (galletitas, snacks, gaseosas y algun basico de almacen)
  y ofrece por unidad antes que por bulto.
- *Almacen de barrio (tamano mediano):* compra surtido amplio de almacen (aceite,
  fideos, arroz, yerba, azucar) — buena oportunidad para vender el bulto y mejorar
  el descuento por volumen.
- *Autoservicio de esquina (mediano/grande):* alta rotacion, compra por bulto y
  suma limpieza y lacteos — recomendar reposicion frecuente para evitar quiebres.

**Ejecucion en punto de venta (POP):**
- Verificar exhibicion ordenada y visible del surtido de mayor rotacion; en
  perecederos (lacteos) revisar rotacion por vencimiento (primero entra, primero
  sale) para evitar mermas.
- Material POP (afiches, habladores de precio) se entrega sin costo; no requiere
  aprobacion especial.
- Sugerir el surtido completo por categoria: un cliente que solo compra almacen es
  una oportunidad para sumarle limpieza o bebidas y aumentar el ticket.

**Frecuencia de visita:** semanal a quincenal, segun tamano de canal (pequeno:
quincenal; mediano/grande: semanal).

**Negociacion:**
- Gira en torno a volumen (venta por bulto) y descuentos por volumen acumulado,
  no por contrato formal.
- Condicion de pago habitual: contado. El credito es mas restringido y requiere
  buen track record (revisar `consultar_historico_pedidos` para ver cumplimiento).
- Argumento de venta: surtido completo en un solo proveedor (almacen + bebidas +
  limpieza) le simplifica la reposicion al comerciante y reduce quiebres.

## 2. Canal Moderno (Modern Trade / MT)

Supermercados e hipermercados, cadenas de autoservicio y mayoristas organizados
(cash & carry).

**Sub-casos frecuentes:**
- *Supermercado de barrio independiente (mediano):* decision la toma el encargado
  de local — negociacion agil, similar a TT pero con mayor volumen por pedido.
- *Cadena regional o hipermercado (grande):* decision la toma una central de
  compras — requiere propuesta formal (volumen mensual, calendario promocional,
  condiciones de pago) y acuerdo de exhibicion en gondola/cabecera.
- *Cash & carry / mayorista organizado:* combina caracteristicas de MT y
  Mayorista — revisar bien el tamano de canal antes de asumir la politica.

**Ejecucion en punto de venta:**
- Acuerdos de gondola/cabecera y exhibiciones adicionales (islas, puntas de
  gondola) se negocian por separado del pedido — coordinar con el area comercial
  si el vendedor lo requiere, vos solo dejalo registrado en una nota.
- Encartes/folletos promocionales de la cadena requieren volumen comprometido
  con antelacion (tipicamente 30-45 dias antes de la fecha de encarte).

**Frecuencia de visita:** semanal (cuentas medianas) a quincenal con seguimiento
telefonico entre visitas (cuentas grandes con contrato).

**Negociacion:**
- Volumen alto por pedido, negociacion con central de compras o encargado de local.
- Condiciones de pago a credito son mas frecuentes aca que en TT.
- Requiere coordinacion logistica con anticipacion (turnos de entrega, documentacion,
  a veces remito con formato especifico de la cadena).
- La politica de descuento aqui es mas agresiva en volumen alto — usar siempre la
  tool de consulta de politica antes de comprometer un numero.

## 3. On Premise / Gastronomia (consumo y preparacion en el lugar)

Restaurantes, bares, rotiserias, comedores, cafeterias — cuentas que consumen o
preparan en el lugar y compran insumos, no para reventa en gondola.

**Sub-casos frecuentes:**
- *Restaurante/rotiseria de barrio:* foco en insumos de cocina (aceite, harina,
  arroz, fideos, conservas, sal) mas bebidas para acompanar y articulos de limpieza.
- *Bar/cafeteria:* alta rotacion de bebidas, cafe, azucar, y descartables/limpieza —
  anticipar reposicion en fines de semana largos o eventos.
- *Comedor/rotiseria de volumen:* compra recurrente y programada de insumos de
  almacen en bulto — tratar como pedido periodico estable.

**Ejecucion en punto de venta:**
- El cliente valora la constancia de abastecimiento: un quiebre de un insumo clave
  (aceite, harina) le frena la cocina. Priorizar que nunca falte el basico.
- Sugerir formatos por bulto/caja para insumos de alto consumo (mejor precio unitario).

**Frecuencia de visita:** alta (semanal), reposicion frecuente por la rotacion rapida.

**Negociacion:**
- Foco en insumos de cocina en bulto y bebidas de acompanamiento.
- Descuento algo mayor en contado que en credito (ver `ontologia-descuentos`).
- Argumento clave: un solo proveedor para almacen, bebidas y limpieza = menos
  ordenes de compra y menos quiebres en el momento de mayor demanda.

## 4. Off Premise

Comercios que revenden para consumo en otro lugar (almacenes, autoservicios de
paso, drugstores). Es una categoria ANALITICA que se solapa con Tradicional y
Moderno — no reemplaza esos canales. Cuando una cuenta se clasifica puntualmente
como "off_premise" (ej. un autoservicio de paso o un maxikiosco 24hs que no encaja
claramente en TT ni MT), la politica de descuento se define por su propio canal,
no por esta etiqueta.

- Predomina el formato de reventa (bulto/caja) sobre la unidad suelta.
- Frecuencia de visita similar a Tradicional.

## 5. Mayoristas / Sub-distribuidores (Wholesale)

Mayoristas y sub-distribuidores que compran en volumen y revenden a comercios mas
chicos, en zonas de menor densidad o donde no se llega en forma directa.

**Sub-casos frecuentes:**
- *Sub-distribuidor regional (grande):* opera con flota propia y revende a decenas
  de comercios — la negociacion es puramente de precio por volumen, sin argumentos
  de exhibicion (el mayorista no exhibe al consumidor final).
- *Mayorista de zona de baja densidad:* unico canal de llegada de producto en
  zonas donde la distribucion directa no es rentable — la relacion es estrategica
  para la cobertura geografica, no solo comercial.

**Negociacion:**
- Pedidos de volumen muy alto (por bulto/pallet), precios mayoristas (mayor
  descuento por volumen — el mas alto de todo el esquema, ver `politicas_descuento`).
- La relacion es de reventa: el mayorista es cliente, no consumidor final — no
  aplican acuerdos de exhibicion en punto de venta.
- Frecuencia de visita baja (mensual o segun ciclo de reposicion propio).
- Condicion de pago: frecuentemente credito, sujeto a linea aprobada por backoffice.

## 6. E-commerce / Canal digital

Tienda mayorista online propia y apps de pedido B2B para comercios.

- No hay visita fisica; la relacion es administrativa/logistica (SLA de entrega,
  disponibilidad de catalogo online, actualizacion de precios en la plataforma).
- Precio estandar de canal digital, descuentos acotados salvo campana puntual
  (ej. dia del almacenero, hot sale, campanas de la plataforma).
- El vendedor en terreno rara vez gestiona esta cuenta dia a dia — su rol es mas
  de seguimiento de catalogo/quiebres que de negociacion recurrente.

## 7. Institucional / Horeca

Comedores corporativos, catering, hoteles, colegios, clubes, eventos — venta B2B a
gran escala, con contratos y logistica diferenciada.

**Sub-casos frecuentes:**
- *Comedor corporativo/catering:* consumo programado y recurrente (almuerzos
  diarios) — pedidos periodicos de volumen estable de insumos de almacen en bulto.
- *Colegio/club/evento puntual:* picos de demanda con poca anticipacion — coordinar
  stock disponible antes de comprometer el volumen.
- *Cadena hotelera:* combina consumo institucional con gastronomia (restaurante del
  hotel) — puede requerir dos acuerdos distintos dentro de la misma cuenta.

**Negociacion:**
- Requiere contrato comercial (volumen mensual comprometido) para acceder a mejores
  condiciones — el mejor descuento del canal exige condicion de pago credito y
  contrato anual (ver `politicas_descuento`).
- Negociacion mas formal: involucra compras corporativas, no solo al encargado local.
- Coordinar entregas programadas y facturacion consolidada.

## 8. Vending

Maquinas expendedoras en oficinas, universidades, estaciones de servicio, edificios
corporativos.

- Reposicion programada, foco en snacks y bebidas en formato individual (galletitas,
  alfajores, gaseosa 500ml, agua 500ml).
- Ubicacion fija: la negociacion es con el administrador del edificio/predio,
  no con un punto de venta tradicional.
- El acuerdo suele incluir surtido acordado para la maquina y reposicion periodica.

## 9. Directo vs. Indirecto (atributo transversal — NO es un canal mas)

- **Directo:** deposito de la distribuidora → reparto propio → punto de venta
  (camiones y depositos propios). Tipico en Moderno, Mayoristas grandes e
  Institucional/Horeca de gran escala, en zonas de alta densidad.
- **Indirecto:** distribuidora → sub-distribuidor/mayorista tercero → minorista.
  Tipico en Tradicional y en zonas de menor densidad donde el reparto directo no
  es rentable.

Este atributo aplica a CUALQUIERA de los 8 canales de arriba y define quien entrega
fisicamente el pedido y quien factura. Consultalo en la ficha de la cuenta
(`consultar_cuenta_cliente`) antes de prometer plazos de entrega — y recorda que
no tenes una tool que confirme tiempos exactos, asi que nunca inventes un plazo.

---

## CONSIDERACIONES ESTACIONALES

- **Verano (diciembre-marzo):** pico de demanda de bebidas, aguas y snacks por el
  calor — sugerir al vendedor anticipar volumen de bebidas en cuentas de tradicional
  y gastronomia antes de la temporada alta.
- **Fiestas de fin de ano (diciembre):** pico de canasta navidena (aceite, harina,
  conservas, dulce de leche, galletitas) para el canal tradicional y moderno —
  anticipar pedidos con 2-3 semanas de margen por la mayor demanda logistica.
- **Invierno (junio-agosto):** sube el consumo de infusiones (yerba, cafe), sopas,
  caldos y guisos (fideos, arroz, legumbres, conservas) — buen momento para reforzar
  el almacen seco en tradicional y gastronomia.
- **Marzo (vuelta a clases y comedores):** repunta el consumo de comedores escolares
  y corporativos — anticipar pedidos institucionales de insumos de almacen en bulto.
- Ninguna promocion estacional se aplica automaticamente: si el vendedor pregunta
  por una campana puntual, consulta `consultar_politica_descuento` — si no
  aparece reflejada ahi, no existe todavia y hay que escalarla.

---

## REGLAS DE ESCALAMIENTO

- Si el cliente pide una condicion fuera de politica (descuento mayor al que
  devuelve `consultar_politica_descuento`, plazo de pago no habitual, credito sin
  linea aprobada, bonificacion especial, etc.): **nunca prometas la excepcion vos
  mismo** — indicale al vendedor que debe escalarlo a su supervisor comercial.
- Si hay un problema con un pedido ya tomado (faltante, defecto, facturacion,
  demora), no lo resuelvas como si fuera una venta nueva: usa
  `abrir_gestion_posventa` para dejarlo trazado formalmente.
- Antes de tomar un pedido nuevo (`crear_pedido`), confirma con el vendedor que
  el cliente acepto el descuento informado — nunca lo asumas.
"""

# ── Ontologia de Politicas de Descuento ──────────────────────────────────────

ONTOLOGIA_DESCUENTOS = """
# Ontologia de Politicas de Descuento — Distribuidora Pampa — v2.0

Esta ontologia explica la LOGICA de las politicas de descuento. El **numero exacto
de descuento SIEMPRE se obtiene de la tool `consultar_politica_descuento`** —
tenes PROHIBIDO calcular, estimar o inventar un porcentaje de descuento vos mismo.
Los porcentajes y casos que aparecen aca son solo ilustrativos, para que entiendas
el criterio — nunca los repitas como si fueran el numero real de un pedido.

## Como se determina un descuento

El descuento aplicable depende de la combinacion de:
1. **Canal** de la cuenta (tradicional, moderno, on_premise, off_premise,
   mayorista, ecommerce, institucional_horeca, vending).
2. **Volumen** del pedido, medido como volumen total en kg/L equivalentes
   (la suma del campo de volumen de cada linea del pedido).
3. **Condicion de pago** (contado o credito) — el credito suele tener menor
   descuento que el contado, salvo en cuentas grandes con contrato anual.
4. **Tamano del canal** (pequeno/mediano/grande) — algunas politicas aplican solo
   a cuentas grandes (ej. contratos anuales en Moderno o Institucional/Horeca).

Cuando hay mas de una politica que podria aplicar, gana la mas especifica (una
fila con tamano de canal definido le gana a una generica del mismo canal, y una
fila de un canal puntual le gana a la fila comodin "todos").

## Logica ilustrativa por canal (referencia para entender el criterio, NO para calcular vos mismo)

- **Tradicional:** descuentos moderados, escalonados por volumen (mas bultos,
  mejor descuento) y mayores en contado que en credito — el credito en este
  canal es mas restringido, asociado a buen historial de pago.
- **Moderno:** el descuento crece fuerte con el volumen. Ejemplo ilustrativo: un
  pedido chico de una cuenta mediana tiene un descuento moderado; el mismo canal
  con volumen alto y, ademas, una cuenta grande con contrato comercial anual en
  credito, accede al mejor escalon del canal.
- **On Premise / Gastronomia:** descuento algo mayor en contado que en credito,
  orientado a insumos de cocina comprados en bulto por restaurantes y comedores.
- **Off Premise:** descuento acotado, pensado para reventa en formato bulto/caja.
- **Mayoristas:** los descuentos mas altos de todo el esquema, por revender en
  volumen — requieren un volumen minimo relevante para aplicar.
- **E-commerce:** descuento acotado y estable, es el precio de canal digital;
  no varia por campanas salvo que exista una politica puntual cargada.
- **Institucional/Horeca:** descuento moderado sin contrato; el mejor descuento
  del canal exige contrato anual de volumen comprometido en condicion credito.
- **Vending:** descuento fijo, no varia mucho por volumen — la negociacion de
  este canal pasa mas por la ubicacion del equipo que por el volumen.

## Acuerdos de exhibicion y promociones estacionales

Los acuerdos de exhibicion (gondola, cabecera, punta de gondola) y las campanas
estacionales (canasta navidena, dia del almacenero) son decisiones comerciales aparte del calculo
de descuento por volumen — si existen, estan cargadas como una politica puntual
en el sistema (por ejemplo con una fila de canal especifico y una nota en
condiciones adicionales) y `consultar_politica_descuento` las va a reflejar. Si
el vendedor pregunta por una promocion que no aparece ahi, **no la inventes**:
indicale que hoy no hay una campana activa para ese caso y que puede escalarla.

## Que hacer si no hay politica aplicable

Si `consultar_politica_descuento` no encuentra una politica especifica, devuelve
0% con un mensaje explicito de "sin politica aplicable, escalar a supervisor
comercial". En ese caso: **nunca ofrezcas un descuento por tu cuenta** — informa
al vendedor que debe escalarlo.

## Casos ilustrativos de uso de la tool

- *"Quiero saber el descuento para un pedido chico (unos 40 de volumen) en un
  kiosco, pago contado"* → llama a `consultar_politica_descuento` con canal=tradicional,
  condicion_pago=contado, volumen_litros=40, tamano_canal=pequeno. Informa
  exactamente lo que devuelve, ni mas ni menos.
- *"El cliente quiere pagar a 30 dias en vez de contado"* → eso es un cambio de
  condicion de pago: volve a consultar la politica con condicion_pago=credito
  para el mismo canal/volumen antes de confirmar nada, el descuento puede cambiar.
- *"¿Le puedo dar el mismo descuento que a la cuenta grande de la otra cuadra?"*
  → no asumas nada de otra cuenta: consulta la politica con los datos reales de
  ESTA cuenta (su propio canal, tamano y condicion de pago).

## Al tomar un pedido (`crear_pedido`)

1. Reuni primero el volumen total estimado (suma del volumen kg/L de todas las lineas).
2. Consulta la politica aplicable con `consultar_politica_descuento` usando el
   canal y condicion de pago de la cuenta.
3. Confirma el descuento con el vendedor ANTES de registrar el pedido.
4. Registra el pedido — siempre queda en estado `solicitado`, pendiente de
   revision de backoffice antes de aprobarse.
"""

# ── Ontologia FAQ ───────────────────────────────────────────────────────────

ONTOLOGIA_FAQ = """
# Ontologia FAQ — Fuerza de Venta Distribuidora Pampa — v2.0

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

**El cliente dice que la competencia le ofrece mejor precio, ¿que argumento uso?**
- No entres en guerra de precios sin autorizacion. Argumenta valor: surtido
  completo en un solo proveedor (almacen + bebidas + limpieza), frecuencia de
  reposicion, menos quiebres, y consulta si
  el volumen del cliente ya esta en el mejor escalon de descuento disponible
  segun `consultar_politica_descuento`. Si no lo esta, mostrale que volumen
  necesitaria para acceder a un descuento mayor.

---

## CONDICIONES DE PAGO

**¿Cuando conviene ofrecer credito en vez de contado?**
- El credito suele tener menor descuento salvo en cuentas grandes con contrato
  anual (Moderno, Institucional/Horeca). Si el cliente quiere credito y no tiene
  linea aprobada, la aprobacion de credito la maneja backoffice, no el vendedor.

**El pedido quedo `rechazado` por backoffice, ¿que hago?**
- Consulta el motivo en las notas del pedido. Si es por credito, abre una gestion
  de posventa de tipo `solicitud_credito` para que backoffice revise la linea.

**¿Se puede pagar parte contado y parte credito en el mismo pedido?**
- No, la condicion de pago es unica por pedido. Si el cliente quiere dividirlo,
  se registran dos pedidos separados, cada uno con su condicion de pago y su
  descuento correspondiente.

---

## LOGISTICA DIRECTA VS INDIRECTA

**¿Como se cual es el tipo de distribucion de una cuenta?**
- Esta en la ficha de la cuenta (`consultar_cuenta_cliente`), campo tipo de
  distribucion: directo (entrega y factura la distribuidora) o indirecto
  (entrega y factura un sub-distribuidor/mayorista tercero).

**¿Puedo cambiar el tipo de distribucion de una cuenta desde el chat?**
- No, es un dato estructural de la cuenta que gestiona el area comercial, no
  una tool disponible para el vendedor en el chat.

**El cliente pregunta cuando le llega el pedido, ¿que le digo?**
- No inventes un tiempo de entrega — no hay una tool que lo confirme. Indica que
  depende de la coordinacion logistica (directa o indirecta segun la cuenta) y
  que debe confirmarse con el area de logistica o backoffice.

---

## CATALOGO Y SKUs

**El cliente pide un producto que no aparece en el catalogo, ¿que hago?**
- Informa que no esta disponible en el catalogo actual y ofrece sugerir un
  producto similar de la misma categoria. No inventes un SKU ni un precio.

**El cliente pregunta por un producto que no esta en el surtido de Pampa, ¿que hago?**
- Aclara con cortesia que trabajas con el surtido de Distribuidora Pampa (almacen,
  galletitas y snacks, bebidas, lacteos, limpieza y perfumeria) y ofrece el
  sustituto mas cercano de la misma categoria en el catalogo propio. No inventes
  un SKU ni un precio.

---

## EJECUCION EN PUNTO DE VENTA Y PERECEDEROS

**¿Como cuido la rotacion de los perecederos (lacteos)?**
- En lacteos y otros perecederos aplica el criterio "primero entra, primero sale":
  sugiere al cliente pedir cantidades acordes a su rotacion para evitar mermas por
  vencimiento. No comprometas cambios ni notas de credito por vencimiento desde el
  chat: si ya ocurrio, abri una gestion de posventa.

**¿Como aumento el ticket de una cuenta que solo compra una categoria?**
- Ofrece el surtido cruzado: a un almacen que solo lleva secos, sumale limpieza o
  bebidas; a una rotiseria que compra insumos, sumale descartables/limpieza. Un
  solo proveedor para varias categorias es el principal argumento de Pampa.

---

## GESTIONES DE POSVENTA

**¿Cuando abro una gestion de posventa en vez de tomar un pedido nuevo?**
- Siempre que el problema sea sobre un pedido YA ENTREGADO/TOMADO: faltante de
  bultos, producto defectuoso, error de facturacion, demora en la entrega
  pactada, o una solicitud de revision de credito. Usa `abrir_gestion_posventa`.

**¿Puedo abrir mas de una gestion para el mismo problema?**
- No — antes de abrir una nueva, usa `consultar_gestiones_posventa` con el
  numero de pedido para verificar si ya existe una gestion abierta para ese caso.

---

## PEDIDOS Y APROBACION

**¿Cuanto tarda en aprobarse un pedido `solicitado`?**
- El pedido queda pendiente de revision de backoffice; no hay un plazo fijo
  que el asistente pueda prometer — informa al vendedor que se hara seguimiento.

**¿Puedo modificar un pedido ya `aprobado` o `facturado`?**
- No mediante las tools de toma de pedido. Cualquier ajuste en ese estado se
  gestiona como una gestion de posventa (`abrir_gestion_posventa`).

**El cliente quiere agregar productos a un pedido que ya quedo `solicitado`, ¿que hago?**
- No se puede editar el detalle de un pedido ya registrado. Registra un pedido
  nuevo con los productos adicionales, o si el original todavia no fue revisado,
  cancelalo (`cancelar_pedido`) y toma uno nuevo con el detalle completo.

---

## ESTACIONALIDAD Y PORTFOLIO

**¿Que productos conviene reforzar en la visita segun la epoca del ano?**
- Verano: bebidas, aguas y snacks. Fin de ano: canasta navidena (aceite, harina,
  conservas, dulce de leche, galletitas). Invierno: infusiones (yerba, cafe) y
  almacen seco para guisos (fideos, arroz, legumbres, conservas). Ver el detalle
  en `ontologia_procedimientos`, seccion de consideraciones estacionales.

**¿Hay descuentos especiales por estacionalidad?**
- Solo si estan cargados como una politica puntual — consulta siempre
  `consultar_politica_descuento`. Si el vendedor pregunta por una campana que no
  aparece reflejada ahi, no existe todavia: no la inventes, indicale que la
  escale para confirmar si corresponde.
"""

# ── System Prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Eres el asistente de fuerza de venta en terreno de Distribuidora Pampa (distribuidora
mayorista de alimentos, bebidas y limpieza). Estas hablando EN
VIVO Y DIRECTAMENTE con el VENDEDOR (no con el cliente final) — la persona
que te escribe en este chat ES el vendedor. Dirigite siempre a el/ella en
segunda persona ("vos", "tenes", "podes"). NUNCA te refieras a "el vendedor"
en tercera persona ni sugieras que "confirme con el vendedor" — eso sos vos
mismo ayudandolo ahora mismo, no hay un tercero al que consultar.

Tu rol no es solo responder preguntas puntuales de forma pasiva: sos un
COACH DE VENTA en tiempo real. Cada respuesta tiene que ayudar activamente
al vendedor a abordar mejor a esa cuenta especifica — que argumento usar
segun el canal, que destacar del historico o del catalogo, como manejar
objeciones de precio o condiciones, y que accion concreta conviene tomar
ahora para mejorar el resultado de la visita. No te limites a informar:
orienta la conversacion hacia cerrar mejor la negociacion.

## Tu flujo de trabajo

1. **Identifica la cuenta**: pedile el codigo de cliente (formato CLI-XXXX) O
   el nombre del comercio — lo que tenga a mano, no le exijas el codigo
   exacto. Si ya viene identificado (por ejemplo porque el vendedor lo
   selecciono desde su cartera antes de iniciar el chat), procede directo al
   paso 2. Si te da un nombre de comercio, una referencia parcial o un codigo
   que `consultar_cuenta_cliente` no encuentra, llama a la tool con lo que
   tengas (`nombre_comercial` y/o `codigo_cliente`, aunque sea parcial): te va
   a devolver una lista de cuentas candidatas por similitud — mostraselas al
   vendedor tal cual y que el confirme cual es. Nunca inventes ni asumas un
   codigo de cuenta.
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
- **PROHIBIDO inventar o asumir un codigo de cuenta.** Si `consultar_cuenta_cliente`
  no encuentra un match exacto, muestra al vendedor la lista de cuentas
  candidatas que devuelve la tool y que el confirme cual es — nunca elijas una
  por tu cuenta.
- **PROHIBIDO inventar tiempos de entrega o plazos logisticos** (por ejemplo
  "24 a 48 horas"). No tenes ninguna tool que confirme tiempos de entrega
  reales. Si te preguntan, indica que el plazo depende de la coordinacion
  logistica (directa o indirecta, segun conste en la ficha de la cuenta) y
  que debe confirmarse con el area de logistica o backoffice — nunca des una
  cifra de horas o dias sin ese respaldo.
- **PROHIBIDO inventar cualquier dato operativo que no devuelva una tool**
  (stock, disponibilidad, plazos, condiciones especiales). Ante la duda,
  decilo con claridad en vez de completar el vacio con una suposicion.

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

AUTOPILOT_CLIENTE = """Eres un vendedor de fuerza de venta en terreno de Distribuidora Pampa que esta usando el
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
Distribuidora Pampa. Evaluaras una conversacion entre un asistente de IA de venta en
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

    # Migracion idempotente para instalaciones ya existentes (evitamos "ADD COLUMN IF NOT
    # EXISTS": en algunos MySQL/MariaDB con plugins de terceros ese patron condicional puede
    # comportarse mal; una consulta a information_schema + ALTER simple es mas portable).
    cur.execute("""
        SELECT COUNT(*) FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'gestiones_posventa' AND column_name = 'contexto_cuenta'
    """)
    if cur.fetchone()[0] == 0:
        print("Agregando columna contexto_cuenta a gestiones_posventa...")
        cur.execute("ALTER TABLE gestiones_posventa ADD COLUMN contexto_cuenta LONGTEXT")

    print("Creando tabla ontologias...")
    cur.execute(CREATE_ONTOLOGIAS)

    # ── Insertar perfil default ──────────────────────────────────────────────
    print("Asegurando perfil 'Distribuidora Pampa' (default)...")
    cur.execute("SELECT id FROM perfiles WHERE nombre = %s", ("Distribuidora Pampa",))
    row = cur.fetchone()
    if row:
        perfil_id = row[0]
        cur.execute("UPDATE perfiles SET activo = 1 WHERE id = %s", (perfil_id,))
        cur.execute("UPDATE perfiles SET activo = 0 WHERE id <> %s", (perfil_id,))
    else:
        cur.execute("""
            INSERT INTO perfiles (nombre, empresa, logo_url, activo)
            VALUES (%s, %s, %s, %s)
        """, ("Distribuidora Pampa", "Distribuidora Pampa S.A.", None, 1))
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

    # SKUs de una version anterior del catalogo (codigos renombrados o
    # discontinuados). Se desactivan en vez de borrarse: pedidos ya sembrados los
    # referencian por FK. En una BD fresca de Pampa esta lista es un no-op.
    _SKUS_OBSOLETOS = (
    )
    cur.executemany(
        "UPDATE productos SET activo = 0 WHERE codigo_sku = %s",
        [(s,) for s in _SKUS_OBSOLETOS],
    )

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

    # ── Insertar politicas de descuento (solo si la tabla esta vacia) ────────
    # No se resiembra en cada corrida para no pisar ediciones hechas desde la UI
    # (panel "Politicas de descuento" en AdminPanel, via /api/politicas-descuento).
    cur.execute("SELECT COUNT(*) FROM politicas_descuento")
    if cur.fetchone()[0] == 0:
        print("Insertando politicas_descuento (seed inicial)...")
        for pol in POLITICAS_DESCUENTO_MOCK:
            cur.execute("""
                INSERT INTO politicas_descuento (canal, tamano_canal, condicion_pago, volumen_min_litros,
                                                  volumen_max_litros, descuento_pct, condiciones_adicionales, prioridad, activo)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, pol)
    else:
        print("politicas_descuento ya tiene datos, no se resiembra (se conservan las ediciones de la UI).")

    # ── Insertar pedidos + detalle + gestiones (solo si la tabla pedidos esta
    # vacia) ──────────────────────────────────────────────────────────────────
    # _gen_pedidos() asume que el producto_id/empresa_cliente_id coincide con la
    # posicion en PRODUCTOS_MOCK/EMPRESAS_CLIENTES_MOCK — solo es valido en una
    # instalacion fresca. En una BD que ya tiene pedidos reales (tomados desde el
    # chat) NO se debe resembrar: se romperia la FK contra el catalogo actual y,
    # sobre todo, se pisarian pedidos reales del negocio.
    cur.execute("SELECT COUNT(*) FROM pedidos")
    if cur.fetchone()[0] == 0:
        print("Insertando pedidos...")
        for ped in PEDIDOS_MOCK:
            cur.execute("""
                INSERT INTO pedidos (numero_pedido, empresa_cliente_id, fecha_pedido, estado, canal_venta,
                                      condicion_pago, vendedor, descuento_aplicado_pct, subtotal, total, notas,
                                      fecha_actualizacion_estado)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE estado = VALUES(estado)
            """, ped)

        print("Insertando detalles de pedido...")
        for det in DETALLES_MOCK:
            cur.execute("""
                INSERT INTO detalle_pedido (numero_pedido, producto_id, cantidad, precio_unitario,
                                             descuento_pct, precio_neto_unitario, subtotal_linea)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE cantidad = VALUES(cantidad)
            """, det)
    else:
        print("La tabla pedidos ya tiene datos, no se resiembran pedidos/detalle mock (se conservan los pedidos reales).")

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
