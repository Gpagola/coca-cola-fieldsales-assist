"""
Script para crear la BBDD SmartAssist y cargar datos mock de retail.
Ejecutar una sola vez: python setup_db.py
"""

import os
import random
from datetime import date, timedelta
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

DB_HOST     = os.getenv("DB_HOST")
DB_PORT     = int(os.getenv("DB_PORT", 3306))
DB_NAME     = os.getenv("DB_NAME", "SmartAssist")
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

CREATE_CLIENTES = """
CREATE TABLE IF NOT EXISTS clientes (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    nombre           VARCHAR(100) NOT NULL,
    email            VARCHAR(150),
    telefono         VARCHAR(20),
    nivel_fidelidad  VARCHAR(20)  NOT NULL DEFAULT 'Basico',
    fecha_registro   DATE         NOT NULL,
    ciudad           VARCHAR(50),
    total_compras    DECIMAL(10,2) NOT NULL DEFAULT 0,
    num_pedidos      INT          NOT NULL DEFAULT 0
);
"""

CREATE_PRODUCTOS = """
CREATE TABLE IF NOT EXISTS productos (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    nombre      VARCHAR(150)   NOT NULL,
    categoria   VARCHAR(50)    NOT NULL,
    precio      DECIMAL(10,2)  NOT NULL,
    stock       INT            NOT NULL DEFAULT 0,
    descripcion TEXT
);
"""

CREATE_PEDIDOS = """
CREATE TABLE IF NOT EXISTS pedidos (
    numero_pedido          VARCHAR(20) PRIMARY KEY,
    cliente_id             INT         NOT NULL,
    fecha_pedido           DATE        NOT NULL,
    estado                 VARCHAR(30) NOT NULL DEFAULT 'pendiente',
    total                  DECIMAL(10,2) NOT NULL,
    metodo_pago            VARCHAR(30),
    direccion_envio        VARCHAR(200),
    tracking               VARCHAR(50),
    fecha_envio            DATE,
    fecha_entrega_estimada DATE,
    fecha_entrega_real     DATE,
    notas                  TEXT,
    FOREIGN KEY (cliente_id) REFERENCES clientes(id)
);
"""

CREATE_DETALLE_PEDIDO = """
CREATE TABLE IF NOT EXISTS detalle_pedido (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    numero_pedido   VARCHAR(20)   NOT NULL,
    producto_id     INT           NOT NULL,
    cantidad        INT           NOT NULL DEFAULT 1,
    precio_unitario DECIMAL(10,2) NOT NULL,
    FOREIGN KEY (numero_pedido) REFERENCES pedidos(numero_pedido),
    FOREIGN KEY (producto_id)   REFERENCES productos(id)
);
"""

CREATE_RECLAMOS = """
CREATE TABLE IF NOT EXISTS reclamos (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    numero_reclamo  VARCHAR(20)  NOT NULL UNIQUE,
    numero_pedido   VARCHAR(20),
    cliente_id      INT          NOT NULL,
    tipo            VARCHAR(30)  NOT NULL,
    descripcion     TEXT         NOT NULL,
    estado          VARCHAR(20)  NOT NULL DEFAULT 'abierto',
    prioridad       VARCHAR(10)  NOT NULL DEFAULT 'media',
    canal           VARCHAR(20)  NOT NULL DEFAULT 'chat',
    fecha_apertura  DATETIME     NOT NULL,
    fecha_cierre    DATETIME,
    resolucion      TEXT,
    FOREIGN KEY (numero_pedido) REFERENCES pedidos(numero_pedido) ON DELETE SET NULL,
    FOREIGN KEY (cliente_id)    REFERENCES clientes(id)
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
PROFILE_ONTOLOGIES = ("system-prompt", "ontologia-procedimientos", "ontologia-faq")

# ── Datos mock: productos ───────────────────────────────────────────────────

PRODUCTOS_MOCK = [
    # Ropa mujer
    ("Vestido Midi Floral", "Ropa Mujer", 49.99, 45, "Vestido midi con estampado floral, tela fluida, perfecto para primavera-verano."),
    ("Blazer Oversize Lino", "Ropa Mujer", 79.99, 30, "Blazer oversize en lino natural, corte relajado, ideal para entretiempo."),
    ("Jeans Wide Leg", "Ropa Mujer", 44.99, 60, "Jeans de pierna ancha, tiro alto, denim 100% algodon."),
    ("Camiseta Basica Algodon Organico", "Ropa Mujer", 19.99, 120, "Camiseta basica en algodon organico certificado, corte recto."),
    ("Falda Plisada Saten", "Ropa Mujer", 39.99, 35, "Falda midi plisada en saten con cintura elastica."),
    # Ropa hombre
    ("Camisa Oxford Slim", "Ropa Hombre", 34.99, 80, "Camisa Oxford de algodon, corte slim fit, cuello con boton."),
    ("Pantalon Chino Regular", "Ropa Hombre", 39.99, 70, "Pantalon chino de algodon stretch, corte regular, 4 colores."),
    ("Sudadera Capucha Basica", "Ropa Hombre", 29.99, 90, "Sudadera con capucha, interior afelpado, bolsillo canguro."),
    ("Polo Pique Classic", "Ropa Hombre", 24.99, 100, "Polo de pique de algodon, corte clasico, logo bordado."),
    ("Bermuda Cargo", "Ropa Hombre", 34.99, 55, "Bermuda cargo con bolsillos laterales, cintura ajustable."),
    # Calzado
    ("Sneakers Urban White", "Calzado", 69.99, 40, "Zapatillas urbanas blancas, suela de goma, piel sintetica."),
    ("Botin Chelsea Piel", "Calzado", 89.99, 25, "Botin Chelsea en piel genuina, elastico lateral, suela track."),
    ("Sandalia Plataforma", "Calzado", 49.99, 30, "Sandalia con plataforma de 5cm, tiras cruzadas, piel sintetica."),
    ("Mocasin Clasico", "Calzado", 59.99, 35, "Mocasin clasico en piel, suela flexible, disponible en negro y marron."),
    ("Deportiva Running Pro", "Calzado", 79.99, 50, "Zapatilla de running con amortiguacion premium y malla transpirable."),
    # Accesorios
    ("Bolso Tote Piel Vegana", "Accesorios", 54.99, 40, "Bolso tote grande en piel vegana, cierre magnetico, bolsillo interior."),
    ("Cinturon Reversible", "Accesorios", 24.99, 60, "Cinturon reversible negro/marron, hebilla metalica clasica."),
    ("Gafas de Sol Retro", "Accesorios", 29.99, 50, "Gafas de sol estilo retro, proteccion UV400, montura acetato."),
    ("Bufanda Lana Merino", "Accesorios", 34.99, 40, "Bufanda en lana merino extra suave, 180x30cm."),
    ("Mochila Urban Canvas", "Accesorios", 44.99, 35, "Mochila urbana en canvas resistente al agua, compartimento portatil."),
    # Hogar / Lifestyle
    ("Vela Aromatica Soja", "Hogar", 18.99, 80, "Vela aromatica de cera de soja, 40h de duracion, aromas naturales."),
    ("Set Toallas Premium", "Hogar", 39.99, 30, "Set de 4 toallas 100% algodon egipcio, 600gsm."),
    ("Cojin Decorativo Lino", "Hogar", 22.99, 45, "Cojin decorativo en lino natural, relleno incluido, 45x45cm."),
    ("Taza Ceramica Artesanal", "Hogar", 14.99, 100, "Taza de ceramica artesanal, 350ml, apta para lavavajillas."),
    ("Difusor de Aromas", "Hogar", 24.99, 50, "Difusor de varillas con aceites esenciales naturales, 200ml."),
]

# ── Datos mock: clientes ────────────────────────────────────────────────────

_NOMBRES = [
    "Maria", "Carlos", "Ana", "Roberto", "Luisa", "Pedro", "Carmen", "Javier",
    "Isabel", "Fernando", "Elena", "Miguel", "Laura", "Antonio", "Sofia",
    "Francisco", "Marta", "Jose", "Patricia", "Manuel", "Raquel", "Alberto",
    "Cristina", "Daniel", "Beatriz", "Alejandro", "Lucia", "David", "Paula",
    "Jorge", "Alicia", "Sergio", "Rosa", "Andres", "Pilar", "Diego",
    "Teresa", "Oscar", "Victoria", "Adrian", "Natalia", "Ruben", "Sara",
    "Enrique", "Irene", "Guillermo", "Claudia", "Marcos", "Eva", "Hugo",
]

_APELLIDOS1 = [
    "Garcia", "Martinez", "Lopez", "Rodriguez", "Jimenez", "Fernandez", "Gomez",
    "Sanchez", "Perez", "Diaz", "Moreno", "Munoz", "Alvarez", "Romero", "Torres",
    "Navarro", "Dominguez", "Vazquez", "Ramos", "Gil", "Serrano", "Blanco",
    "Molina", "Morales", "Suarez", "Ortega",
]

_APELLIDOS2 = [
    "Ruiz", "Hernandez", "Castro", "Vargas", "Medina", "Herrera", "Delgado",
    "Pena", "Cruz", "Flores", "Reyes", "Aguilar", "Leon", "Campos", "Vega",
    "Prieto", "Fuentes", "Cabrera", "Calvo", "Mendez",
]

_CIUDADES = [
    "Madrid", "Barcelona", "Valencia", "Sevilla", "Bilbao", "Malaga",
    "Zaragoza", "Alicante", "Murcia", "Palma de Mallorca", "Las Palmas",
    "Valladolid", "Vigo", "Gijon", "Granada", "A Coruna",
]

_NIVELES = ["Basico", "Premium", "VIP"]
_METODOS_PAGO = ["Tarjeta credito", "Tarjeta debito", "PayPal", "Bizum", "Transferencia"]
_ESTADOS_PEDIDO = ["pendiente", "procesando", "enviado", "entregado", "cancelado", "devuelto"]

random.seed(42)


def _gen_clientes(n=50):
    clientes = []
    used_names = set()
    for _ in range(n):
        while True:
            nombre = f"{random.choice(_NOMBRES)} {random.choice(_APELLIDOS1)} {random.choice(_APELLIDOS2)}"
            if nombre not in used_names:
                used_names.add(nombre)
                break
        email_base = nombre.lower().replace(" ", ".").replace("a", "a").replace("e", "e")
        email = f"{email_base}@email.com"
        telefono = f"+34 6{random.randint(10, 99)} {random.randint(100, 999)} {random.randint(100, 999)}"
        nivel = random.choices(_NIVELES, weights=[50, 35, 15])[0]
        fecha_reg = date(2020, 1, 1) + timedelta(days=random.randint(0, (date(2025, 12, 31) - date(2020, 1, 1)).days))
        ciudad = random.choice(_CIUDADES)
        total_compras = round(random.uniform(50, 5000), 2)
        num_pedidos = random.randint(1, 30)
        clientes.append((nombre, email, telefono, nivel, fecha_reg.isoformat(), ciudad, total_compras, num_pedidos))
    return clientes


def _gen_pedidos(num_clientes, num_productos, n=100):
    pedidos = []
    detalles = []
    for i in range(1, n + 1):
        numero = f"PED-{i:04d}"
        cliente_id = random.randint(1, num_clientes)
        fecha = date(2024, 6, 1) + timedelta(days=random.randint(0, 300))
        # Distribucion realista de estados
        estado = random.choices(
            _ESTADOS_PEDIDO,
            weights=[10, 10, 15, 45, 10, 10]
        )[0]
        metodo_pago = random.choice(_METODOS_PAGO)
        ciudad = random.choice(_CIUDADES)
        direccion = f"Calle {random.choice(['Mayor', 'Gran Via', 'Alcala', 'Serrano', 'Diagonal', 'Ramblas', 'Paseo de Gracia'])} {random.randint(1, 150)}, {ciudad}"

        # Generar items del pedido (1-4 productos)
        num_items = random.randint(1, 4)
        productos_pedido = random.sample(range(1, num_productos + 1), min(num_items, num_productos))
        total = 0.0
        for prod_id in productos_pedido:
            cantidad = random.randint(1, 3)
            # El precio se fijara al insertar, aqui usamos indice
            precio_idx = prod_id - 1
            precio = PRODUCTOS_MOCK[precio_idx][2] if precio_idx < len(PRODUCTOS_MOCK) else 29.99
            subtotal = precio * cantidad
            total += subtotal
            detalles.append((numero, prod_id, cantidad, precio))
        total = round(total, 2)

        tracking = f"ES{random.randint(100000000, 999999999)}TR" if estado in ("enviado", "entregado") else None
        fecha_envio = (fecha + timedelta(days=random.randint(1, 3))) if estado in ("enviado", "entregado") else None
        fecha_entrega_est = (fecha_envio + timedelta(days=random.randint(2, 5))) if fecha_envio else None
        fecha_entrega_real = (fecha_entrega_est + timedelta(days=random.randint(-1, 2))) if estado == "entregado" and fecha_entrega_est else None

        notas = None
        if estado == "cancelado":
            notas = random.choice([
                "Cliente solicito cancelacion antes del envio",
                "Cancelado por falta de stock",
                "Cliente cambio de opinion",
            ])
        elif estado == "devuelto":
            notas = random.choice([
                "Talla incorrecta, solicita cambio",
                "Producto no coincide con la foto",
                "Llego danado",
                "No le gusto el color",
            ])

        pedidos.append((
            numero, cliente_id, fecha.isoformat(), estado, total, metodo_pago,
            direccion, tracking,
            fecha_envio.isoformat() if fecha_envio else None,
            fecha_entrega_est.isoformat() if fecha_entrega_est else None,
            fecha_entrega_real.isoformat() if fecha_entrega_real else None,
            notas,
        ))
    return pedidos, detalles


CLIENTES_MOCK = _gen_clientes(50)
PEDIDOS_MOCK, DETALLES_MOCK = _gen_pedidos(50, len(PRODUCTOS_MOCK), 100)


def _gen_reclamos(pedidos):
    """Genera reclamos mock: uno por cada pedido devuelto + algunos para pedidos enviados/entregados."""
    reclamos = []
    contador = 1
    for ped in pedidos:
        numero_pedido, cliente_id, fecha_str, estado = ped[0], ped[1], ped[2], ped[3]
        fecha_base = date.fromisoformat(fecha_str)

        if estado == "devuelto":
            tipo = random.choice(["defecto", "otro"])
            desc = random.choice([
                "Producto llego con un defecto de fabrica visible.",
                "El producto no coincide con la descripcion de la web.",
                "La talla no corresponde al talle indicado.",
            ])
            resultado = random.choice(["resuelto", "cerrado"])
            fecha_apertura = fecha_base + timedelta(days=random.randint(5, 15))
            fecha_cierre = fecha_apertura + timedelta(days=random.randint(2, 10))
            resolucion = "Devolucion aceptada y reembolso procesado."
            reclamos.append((
                f"REC-{contador:04d}", numero_pedido, cliente_id, tipo, desc,
                resultado, "media", "chat",
                fecha_apertura.isoformat() + " 10:00:00",
                fecha_cierre.isoformat() + " 12:00:00",
                resolucion,
            ))
            contador += 1
        elif estado == "entregado" and random.random() < 0.08:
            tipo = random.choice(["atencion", "retraso", "cobro_indebido"])
            desc = random.choice([
                "El cliente reporta cargo duplicado en su tarjeta.",
                "Entrega retrasada respecto a la fecha comprometida.",
                "Queja sobre la atencion recibida en un contacto previo.",
            ])
            fecha_apertura = fecha_base + timedelta(days=random.randint(1, 20))
            reclamos.append((
                f"REC-{contador:04d}", numero_pedido, cliente_id, tipo, desc,
                "en_gestion", "alta", "chat",
                fecha_apertura.isoformat() + " 15:30:00",
                None, None,
            ))
            contador += 1
    return reclamos


RECLAMOS_MOCK = _gen_reclamos(PEDIDOS_MOCK)


# ── Ontologia de Procedimientos ──────────────────────────────────────────────

ONTOLOGIA_PROCEDIMIENTOS = """
# Ontologia de Procedimientos de Atencion al Cliente — v1.0

Eres un experto en atencion al cliente para retail de moda y lifestyle.
A continuacion se definen los procedimientos por tipo de consulta.

---

## COMO GESTIONAR CADA TIPO DE CONSULTA

### Segun el nivel de fidelidad del cliente
- **VIP:** maxima prioridad. Ofrece soluciones inmediatas, envio express gratuito en reenvios, y gestion personalizada. Este cliente merece un trato excepcional.
- **Premium:** prioriza soluciones rapidas. Ofrece opciones flexibles y muestra interes genuino por su satisfaccion.
- **Basico:** atencion cordial y eficiente. Sigue los procedimientos estandar pero siempre con empatia.

### Como avanzar en la conversacion
- Presenta **una solucion por turno**. Espera la reaccion del cliente antes de continuar.
- Si el cliente acepta: confirma los detalles y ejecuta la accion.
- Si el cliente no esta satisfecho: ofrece la **siguiente alternativa** disponible.
- Nunca repitas una solucion que el cliente ya rechazo.

---

## CONSULTA: Estado del pedido

**Procedimiento:**
1. Pedir el numero de pedido o datos del cliente para localizarlo.
2. Buscar el pedido con la tool `buscar_pedido`.
3. Informar el estado actual de forma clara:
   - **Pendiente:** "Tu pedido esta confirmado y estamos preparandolo."
   - **Procesando:** "Tu pedido esta siendo preparado en nuestro almacen."
   - **Enviado:** "Tu pedido ya esta en camino. Tracking: [numero]. Entrega estimada: [fecha]."
   - **Entregado:** "Segun nuestro sistema, tu pedido fue entregado el [fecha]."
4. Si el cliente dice que no lo recibio y el estado es 'entregado', abrir incidencia.

---

## CONSULTA: Devolucion / Cambio de producto

**Procedimiento:**
1. Verificar que el pedido esta dentro del plazo de devolucion (30 dias desde la entrega).
2. Preguntar el motivo de la devolucion (talla, color, defecto, no gusta).
3. Ofrecer opciones segun el motivo:
   - **Talla incorrecta:** cambio por la talla correcta sin coste. Verificar stock.
   - **Defecto / danado:** disculparse, ofrecer reenvio inmediato o reembolso completo.
   - **No gusta / cambio de opinion:** devolucion con envio de devolucion gratuito para Premium/VIP; Basico paga envio de devolucion (5.99 EUR).
4. Explicar el proceso: generar etiqueta de devolucion, reembolso en 5-7 dias habiles tras recibir el producto.

**Si el plazo ha expirado:**
- Informar con empatia que el plazo de 30 dias ha pasado.
- Para clientes VIP: ofrecer excepcion como gesto comercial (hasta 45 dias).
- Para otros: sugerir contactar con la tienda fisica mas cercana.

---

## CONSULTA: Problema con el envio / Retraso

**Procedimiento:**
1. Buscar el pedido y verificar el estado de tracking.
2. Si esta en plazo normal (2-5 dias habiles): tranquilizar al cliente.
3. Si hay retraso (>5 dias habiles desde envio):
   - Disculparse por el retraso.
   - Contactar con la empresa de transporte (simular gestion).
   - Ofrecer seguimiento prioritario.
   - Si el retraso es >10 dias: ofrecer reenvio o reembolso.
4. Para clientes VIP/Premium con retraso: ofrecer un descuento del 10% en proxima compra como compensacion.

---

## CONSULTA: Cancelacion de pedido

**Procedimiento:**
1. Verificar el estado del pedido.
2. Si esta **pendiente o procesando:** cancelacion inmediata, reembolso en 3-5 dias.
3. Si esta **enviado:** informar que ya no se puede cancelar, pero puede rechazar la entrega o hacer devolucion gratuita.
4. Preguntar siempre el motivo de la cancelacion para feedback.

---

## CONSULTA: Informacion de producto / Disponibilidad

**Procedimiento:**
1. Buscar el producto con la tool `buscar_producto`.
2. Informar: nombre, precio, disponibilidad de stock, descripcion.
3. Si no hay stock: ofrecer avisar cuando se reponga, o sugerir productos similares de la misma categoria.
4. Si el cliente pregunta por tallas: informar las disponibles.

---

## CONSULTA: Facturacion / Pago

**Procedimiento:**
1. Para solicitud de factura: pedir datos fiscales y numero de pedido, confirmar que se enviara por email en 24h.
2. Para cargo duplicado: verificar el pedido, abrir incidencia con el equipo de pagos, confirmar resolucion en 48h.
3. Para cambio de metodo de pago: solo posible si el pedido esta en estado 'pendiente'.

---

## CONSULTA: Queja / Reclamacion

**Procedimiento:**
1. Escuchar activamente sin interrumpir.
2. Disculparse de forma genuina y especifica.
3. Identificar el problema raiz y ofrecer solucion concreta.
4. Si el cliente insiste: ofrecer escalar a supervisor.
5. Registrar la queja en el sistema para seguimiento.

---

## ACCIONES EJECUTIVAS — Tools de escritura

Ademas de informar, dispones de tools que **modifican el pedido en el sistema**.
Nunca prometas una accion sin ejecutarla: cuando el cliente acepta, invoca la tool correspondiente.

### Cuando invocar cada tool

1. **`cancelar_pedido(numero_pedido, motivo)`** — Cuando el cliente confirma explicitamente que quiere cancelar.
   - Antes: confirma con el cliente ("Confirmas que quieres cancelar el pedido X?").
   - Requiere motivo. Si no lo da, preguntalo brevemente.
   - Solo funciona si el estado es 'pendiente' o 'procesando'. La tool rechazara el resto.

2. **`actualizar_direccion_envio(numero_pedido, nueva_direccion)`** — Cuando el cliente quiere corregir o cambiar la direccion.
   - Pide la direccion completa (calle, numero, piso, ciudad, codigo postal) en un solo mensaje.
   - Solo funciona si el pedido aun no ha salido (sin fecha_envio).

3. **`cambiar_metodo_pago(numero_pedido, nuevo_metodo)`** — Cuando el cliente quiere cambiar la forma de pago.
   - Metodos aceptados: tarjeta, paypal, bizum, transferencia, contra_reembolso.
   - Solo funciona si el estado es 'pendiente'.

4. **`iniciar_devolucion(numero_pedido, motivo)`** — Cuando el cliente confirma que quiere devolver un pedido ya entregado.
   - Antes de invocarla, sigue el procedimiento de **CONSULTA: Devolucion / Cambio de producto**:
     pregunta el motivo y ofrece la alternativa adecuada. Solo llama a la tool cuando el cliente **acepta** la devolucion.
   - Solo funciona si el estado es 'entregado' y dentro del plazo (30 dias, 45 VIP).

5. **`marcar_incidencia_entrega(numero_pedido, descripcion)`** — Cuando el cliente dice "no lo recibi" y el pedido figura como entregado.
   - Registra la incidencia sin cambiar el estado. Logistica investigara.
   - Explica al cliente que recibira respuesta en 48h con numero de caso.

6. **`agregar_nota_pedido(numero_pedido, nota)`** — Cuando hay que dejar trazado algo que no encaja en otras tools:
   - Acuerdo de descuento de compensacion, peticion especial, feedback de queja, preferencia del cliente.
   - Usar DESPUES de una queja para registrarla formalmente.

7. **`abrir_reclamo(numero_pedido, tipo, descripcion)`** — Cuando el problema requiere trazabilidad formal e intervencion del equipo:
   - **defecto:** producto danado o con tara de fabrica.
   - **no_recibido:** cliente insiste en que no recibio un pedido entregado (escalar DESPUES de `marcar_incidencia_entrega` si el cliente lo requiere).
   - **cobro_indebido:** cargo duplicado o no reconocido — requiere intervencion del equipo de pagos.
   - **retraso:** retraso grave que ya no se puede resolver solo con seguimiento.
   - **atencion:** queja formal sobre atencion previa, supervisor, etc.
   - **otro:** cualquier caso que no encaje en los anteriores.
   Genera un REC-XXXX y debe comunicarse **siempre** al cliente como referencia.
   La prioridad y el SLA se asignan automaticamente segun el nivel del cliente.

8. **`consultar_reclamos(numero)`** — Lectura:
   - Si el cliente da un REC-XXXX, consulta el estado del reclamo.
   - Si quieres saber si un pedido ya tiene reclamos antes de abrir otro, pasa el PED-XXXX.
   - Util al inicio de una queja para no duplicar reclamos existentes.

### Cuando abrir un reclamo vs solo anotar

- **Reclamo formal** (`abrir_reclamo`): hay un compromiso de respuesta con SLA, requiere equipo externo (logistica, pagos, supervisor), o el cliente pide escalar.
- **Solo nota** (`agregar_nota_pedido`): el caso se resolvio en el chat pero conviene dejar traza.
- **Solo incidencia** (`marcar_incidencia_entrega`): primer registro de "no lo recibi"; el reclamo se abre despues si el cliente lo requiere o logistica no resuelve.

### Reglas de uso

- **Nunca inventes el resultado de una tool.** Si una tool devuelve un mensaje de error o limitacion,
  comunicaselo al cliente tal cual, adaptando el tono.
- **Confirma siempre antes de ejecutar** una accion destructiva (cancelar, devolver, cambiar direccion o pago).
- **Una sola tool por turno** cuando sea posible, para que el cliente siga el hilo.
- Tras ejecutar una accion exitosa, resume al cliente **que hiciste** y **que sigue**
  (plazos de reembolso, proximos pasos, numero de caso).

---

## NOTAS GENERALES PARA EL AGENTE

- Siempre personalizar con el nombre del cliente y datos de su pedido.
- Un cliente VIP merece un esfuerzo extra — usar su historial para personalizar.
- No prometer plazos que no se puedan cumplir.
- Si no se puede resolver en el momento, dar un plazo concreto de respuesta.
- Cerrar siempre con una pregunta: "Hay algo mas en lo que pueda ayudarte?"
"""

# ── Ontologia FAQ ───────────────────────────────────────────────────────────

ONTOLOGIA_FAQ = """
# Ontologia FAQ — Preguntas Frecuentes v1.0

Respuestas estandar para las preguntas mas habituales de los clientes.
El agente debe adaptar el tono y personalizar con los datos del cliente.

---

## ENVIOS

**Cuanto tarda el envio?**
- Envio estandar: 3-5 dias habiles.
- Envio express (Premium/VIP gratuito, Basico +4.99 EUR): 24-48h.
- Envio a Baleares/Canarias: 5-7 dias habiles.

**Cuanto cuesta el envio?**
- Gratuito para pedidos superiores a 50 EUR.
- Pedidos menores: 3.99 EUR envio estandar.
- Clientes VIP: envio gratuito siempre.

**Hacen envios internacionales?**
- Actualmente enviamos a Espana peninsular, Baleares y Canarias.
- Envios a Portugal disponibles (5-7 dias, 6.99 EUR).
- Resto de Europa: no disponible actualmente.

---

## DEVOLUCIONES

**Cual es el plazo de devolucion?**
- 30 dias naturales desde la recepcion del pedido.
- El producto debe estar sin usar, con etiquetas y en su embalaje original.
- Productos de bano/intimo no admiten devolucion por higiene.

**Como hago una devolucion?**
1. Contactar con atencion al cliente (aqui mismo o por email).
2. Recibes una etiqueta de devolucion por email.
3. Dejar el paquete en un punto de recogida Correos/SEUR.
4. Reembolso en 5-7 dias habiles tras recibir el producto.

**Me devuelven el dinero del envio?**
- Si la devolucion es por defecto nuestro: reembolso completo incluyendo envio.
- Si es por cambio de opinion: solo se reembolsa el producto, no el envio original.

---

## PAGOS

**Que metodos de pago aceptan?**
- Tarjeta de credito/debito (Visa, Mastercard, Amex).
- PayPal.
- Bizum.
- Transferencia bancaria (solo pedidos >100 EUR).

**Es seguro comprar en la web?**
- Si, usamos encriptacion SSL y cumplimos con la normativa PSD2.
- Los datos de pago no se almacenan en nuestros servidores.
- Certificado de confianza online.

**Puedo pagar a plazos?**
- Disponible para pedidos >75 EUR a traves de Klarna (3 plazos sin intereses).

---

## PROGRAMA DE FIDELIDAD

**Como funciona el programa de fidelidad?**
- **Basico (0-499 EUR/ano):** acceso a newsletter y ofertas generales.
- **Premium (500-1499 EUR/ano):** envio express gratuito, acceso anticipado a rebajas, descuento cumpleanos 15%.
- **VIP (1500+ EUR/ano):** todo lo anterior + envio siempre gratuito, atencion prioritaria, invitaciones a eventos, descuento permanente 10%.

**Como subo de nivel?**
- Automaticamente al alcanzar el umbral de gasto anual.
- El nivel se recalcula cada 1 de enero.

---

## TIENDAS FISICAS

**Puedo recoger en tienda?**
- Si, opcion "Click & Collect" disponible. Recogida en 2h si hay stock en tienda.
- Envio a tienda gratuito si el producto no esta en la tienda local.

**Puedo devolver en tienda un pedido online?**
- Si, puedes devolver en cualquier tienda fisica con el numero de pedido.
- Reembolso inmediato en el mismo metodo de pago.

---

## OTROS

**Tienen tallas grandes/especiales?**
- Rango de tallas de XS a XXL en la mayoria de productos.
- Coleccion Plus Size disponible en seleccion de productos.

**Los productos son sostenibles?**
- Linea "Conscious" fabricada con materiales reciclados y organicos.
- Embalajes 100% reciclables.
- Programa de recogida de ropa usada en tiendas.

**Como contacto con atencion al cliente?**
- Chat en vivo (este canal): lunes a sabado 9h-21h.
- Email: ayuda@urbanstyle.es — respuesta en 24h.
- Telefono: 900 123 456 (gratuito) — lunes a viernes 9h-18h.
"""

# ── System Prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Eres un asistente experto en atencion al cliente para una tienda de moda y lifestyle.

## Tu flujo de trabajo

1. **Saluda** al cliente de forma amable y pregunta en que puedes ayudarle.
2. **Identifica la consulta** del cliente: pedido, devolucion, producto, queja, etc.
3. Si el cliente menciona un numero de pedido, **busca el pedido** con la tool `buscar_pedido`.
4. Si el cliente pregunta por un producto, **busca el producto** con la tool `buscar_producto`.
5. **Consulta los procedimientos** usando la tool `ontologia_procedimientos` para obtener los pasos a seguir segun el tipo de consulta.
6. Si el cliente tiene una pregunta general, **consulta las FAQ** con la tool `ontologia_faq`.
7. **Guia al cliente** paso a paso hasta resolver su consulta.
8. Al finalizar, pregunta si necesita ayuda con algo mas.

## Formato de respuesta — OBLIGATORIO

Responde siempre de forma:
- Clara y concisa (maximo 3-4 oraciones por turno).
- Empatica y profesional.
- Personalizada con el nombre del cliente cuando lo tengas.

Cuando ofrezcas una solucion:
- Explica brevemente que vas a hacer.
- Confirma los detalles relevantes.
- Da un plazo concreto si aplica.

## Reglas de procedimiento — OBLIGATORIAS

- **PROHIBIDO inventar procedimientos.** Solo puedes seguir los pasos que estan en la ontologia de procedimientos. Si un caso no esta cubierto, informa al cliente que vas a escalar su consulta.
- **PROHIBIDO inventar informacion de FAQ.** Solo usa respuestas que esten en la ontologia FAQ. Si no esta, di que vas a consultar y responder por email.
- **PROHIBIDO dar varios pasos a la vez.** Una accion por turno, espera confirmacion del cliente.
- **PROHIBIDO inventar datos del pedido.** Usa solo lo que devuelve `buscar_pedido`.
- **PROHIBIDO prometer plazos o compensaciones** que no esten en los procedimientos.

## Documentos adjuntos

El cliente puede adjuntar fotos o documentos (producto danado, factura, etc.). Cuando lo haga:
- Usa la informacion del analisis proporcionado.
- Si es una foto de producto danado: inicia procedimiento de devolucion por defecto.
- Si es una factura: usala para localizar el pedido.

## Perfil del cliente — como usarlo

Cuando `buscar_pedido` devuelva datos del cliente, tenlos en cuenta:
- **Nombre:** personaliza la conversacion (ej: "Maria, entiendo tu preocupacion...").
- **Nivel de fidelidad:** adapta las opciones segun el nivel (VIP > Premium > Basico).
- **Historial:** si tiene muchos pedidos, reconoce su fidelidad.
- **Ciudad:** usa para informar sobre tiendas fisicas cercanas o plazos de envio.

## Reglas generales

- Habla siempre en espanol, de forma profesional pero cercana.
- Nunca inventes datos — usa solo lo que devuelven las tools.
- Tu objetivo es resolver la consulta del cliente de forma eficiente y dejar una buena experiencia.
- Si no puedes resolver algo, ofrece escalar y da un plazo de respuesta.
"""


# ── Autopilot: prompts globales (perfil_id IS NULL) ─────────────────────────

AUTOPILOT_CLIENTE = """Eres un cliente de una tienda (retail) que contacta con el servicio de atencion al cliente a traves del chat. Estas hablando DIRECTAMENTE con el asistente de la tienda (no hay ejecutivo intermediario).

Datos de tu caso:
- Tu nombre: {cliente}
- Tu nivel de fidelidad: {nivel_fidelidad}
- Tu pedido: {numero_pedido} (estado actual: {estado})
- Motivo de tu consulta: {motivo}
- Tu personalidad: {personalidad}

Instrucciones:
- Responde SIEMPRE en 1-3 frases cortas, como escribiria un cliente real en un chat.
- Si es tu primer mensaje (primer_turno=True), plantea tu problema de forma natural y directa, como lo harias al abrir un chat de soporte. Puedes mencionar el numero de pedido si es relevante al motivo.
- Si el asistente te pide el numero de pedido, tu email o datos, proporcionalos segun tu caso.
- Eres un cliente REALISTA: si el asistente te da una respuesta clara, resuelve tu duda o te propone una solucion concreta, muestrate satisfecho y cierra la conversacion con [DECISION: RESUELTO].
- Si el asistente no entiende tu problema, da respuestas genericas, te ignora o no resuelve nada tras varios intentos, muestrate insatisfecho y cierra con [DECISION: NO_RESUELTO].
- No decidas antes del turno 3. Dale al asistente la oportunidad de ayudarte.
- Si el asistente se despide o cierra la conversacion, decide INMEDIATAMENTE con [DECISION: RESUELTO] o [DECISION: NO_RESUELTO] segun si te ayudo o no.
- NUNCA respondas como asistente, agente ni ejecutivo. Solo eres el cliente escribiendo en el chat."""


AUTOPILOT_EVALUADOR = """Eres un experto en calidad de atencion al cliente para comercio retail.
Evaluaras una conversacion entre un asistente de IA de atencion al cliente y un cliente final.

Debes evaluar en 3 dimensiones y dar recomendaciones concretas de mejora para cada nivel de la ontologia:

1. **system-prompt**: instrucciones generales del agente (tono, estructura, empatia, personalizacion)
2. **ontologia-procedimientos**: pasos y procesos de atencion segun el tipo de consulta (estado de pedido, devoluciones, retrasos, cancelaciones, producto defectuoso, quejas)
3. **ontologia-faq**: preguntas frecuentes sobre politicas y funcionamiento de la tienda (envios, devoluciones, pagos, fidelidad)

Para cada dimension:
- Score del 1 al 10
- Lista de problemas detectados (puede estar vacia)
- Recomendacion concreta de texto a agregar/modificar en esa ontologia

RESTRICCION CRITICA — todas las recomendaciones deben basarse EXCLUSIVAMENTE en:
- Mejorar la claridad, empatia y personalizacion del discurso
- Uso mas efectivo de los datos disponibles (pedido, cliente, nivel de fidelidad, historial)
- Mejorar los procedimientos y pasos a seguir en cada tipo de gestion
- Completar o precisar informacion de FAQ sobre politicas existentes

NUNCA recomiendes ni insinues:
- Descuentos, bonificaciones, promociones o regalos
- Cambios en la politica de precios, envios o devoluciones
- Compensaciones economicas o cupones
- Cualquier accion que implique coste o aprobacion comercial

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
      "recomendacion": "En la seccion de devoluciones, agregar: ..."
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
    # Paso 1: Crear la BBDD si no existe (conectar sin especificar database)
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

    # Paso 2: Conectar a la nueva BBDD y crear tablas
    print(f"Conectando a '{DB_NAME}'...")
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor()

    print("Creando tabla clientes...")
    cur.execute(CREATE_CLIENTES)

    print("Creando tabla productos...")
    cur.execute(CREATE_PRODUCTOS)

    print("Creando tabla perfiles...")
    cur.execute(CREATE_PERFILES)

    print("Creando tabla pedidos...")
    cur.execute(CREATE_PEDIDOS)

    print("Creando tabla detalle_pedido...")
    cur.execute(CREATE_DETALLE_PEDIDO)

    print("Creando tabla reclamos...")
    cur.execute(CREATE_RECLAMOS)

    print("Creando tabla ontologias...")
    cur.execute(CREATE_ONTOLOGIAS)

    # ── Insertar perfil default ──────────────────────────────────────────────
    print("Asegurando perfil 'Urban Style' (default)...")
    cur.execute("SELECT id FROM perfiles WHERE nombre = %s", ("Urban Style",))
    row = cur.fetchone()
    if row:
        perfil_id = row[0]
        cur.execute("UPDATE perfiles SET activo = 1 WHERE id = %s", (perfil_id,))
        cur.execute("UPDATE perfiles SET activo = 0 WHERE id <> %s", (perfil_id,))
    else:
        cur.execute("""
            INSERT INTO perfiles (nombre, empresa, logo_url, activo)
            VALUES (%s, %s, %s, %s)
        """, ("Urban Style", "Urban Style S.L.", None, 1))
        perfil_id = cur.lastrowid

    # ── Insertar productos ───────────────────────────────────────────────────
    print("Insertando productos...")
    for prod in PRODUCTOS_MOCK:
        cur.execute("""
            INSERT INTO productos (nombre, categoria, precio, stock, descripcion)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                precio = VALUES(precio),
                stock  = VALUES(stock)
        """, prod)

    # ── Insertar clientes ────────────────────────────────────────────────────
    print("Insertando clientes...")
    for cli in CLIENTES_MOCK:
        cur.execute("""
            INSERT INTO clientes (nombre, email, telefono, nivel_fidelidad,
                                  fecha_registro, ciudad, total_compras, num_pedidos)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE nombre = VALUES(nombre)
        """, cli)

    # ── Insertar pedidos ─────────────────────────────────────────────────────
    print("Insertando pedidos...")
    for ped in PEDIDOS_MOCK:
        cur.execute("""
            INSERT INTO pedidos (numero_pedido, cliente_id, fecha_pedido, estado, total,
                                 metodo_pago, direccion_envio, tracking,
                                 fecha_envio, fecha_entrega_estimada, fecha_entrega_real, notas)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE estado = VALUES(estado)
        """, ped)

    # ── Insertar detalles de pedido ──────────────────────────────────────────
    print("Insertando detalles de pedido...")
    for det in DETALLES_MOCK:
        cur.execute("""
            INSERT INTO detalle_pedido (numero_pedido, producto_id, cantidad, precio_unitario)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE cantidad = VALUES(cantidad)
        """, det)

    # ── Insertar reclamos ────────────────────────────────────────────────────
    print(f"Insertando {len(RECLAMOS_MOCK)} reclamos mock...")
    for rec in RECLAMOS_MOCK:
        cur.execute("""
            INSERT INTO reclamos (numero_reclamo, numero_pedido, cliente_id, tipo, descripcion,
                                  estado, prioridad, canal, fecha_apertura, fecha_cierre, resolucion)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE estado = VALUES(estado)
        """, rec)

    # ── Insertar ontologias ──────────────────────────────────────────────────
    print("Insertando ontologia-procedimientos...")
    cur.execute("""
        INSERT INTO ontologias (nombre, version, contenido, activo, perfil_id)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE contenido = VALUES(contenido), activo = VALUES(activo)
    """, ("ontologia-procedimientos", "1.0", ONTOLOGIA_PROCEDIMIENTOS, 1, perfil_id))

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
    # (No se puede usar INSERT IGNORE aqui: MySQL trata NULL != NULL en unique keys.)
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
    print("\nSetup completado. Base de datos 'SmartAssist' lista.")


if __name__ == "__main__":
    setup()
