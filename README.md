# Coca-Cola Field Sales Assist

Asistente conversacional para la fuerza de venta en terreno de Coca-Cola. Ayuda al vendedor a preparar el pitch de venta y la negociación con cada cuenta (canal tradicional, moderno, on/off premise, mayoristas, e-commerce, institucional/Horeca, vending), consultando el perfil y el histórico de la cuenta, el catálogo de productos, y aplicando de forma determinista las políticas de descuento vigentes según canal, volumen y condición de pago.

Construido sobre un agente LangChain + LangGraph con tools, ontologías versionadas en base de datos (procedimientos por canal, políticas de descuento, FAQ) y un backend Flask + frontend React.

Proyecto derivado de una copia interna de SMART-assist, adaptado íntegramente al dominio B2B de Coca-Cola (modelo de datos, tools, ontologías y frontend).
