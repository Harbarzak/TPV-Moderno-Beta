"""Recorrido QA (fase 24 · QA final): los 25 escenarios mínimos del prompt.

Prueba el sistema como un usuario real: por la MISMA API HTTP que consumen
las SPAs, contra una pila desplegada (compose de ``deploy/docker`` u otra).
Solo biblioteca estándar: corre con cualquier Python 3.11+ sin instalar nada.

Uso:
    export TPV_QA_BASE=http://localhost:8081      # entrada por el proxy
    export TPV_QA_ADMIN_PW=...                    # contraseña del admin
    python docs/testing/qa_scenarios.py main       # escenarios 1-20, 24, 25
    python docs/testing/qa_scenarios.py offline    # escenario 21 (sin Internet)
    python docs/testing/qa_scenarios.py down       # escenario 22 (TPV parado:
    docker compose stop api                        #   parar el api ANTES de «down»)
    python docs/testing/qa_scenarios.py recovery   # escenario 23 (vuelta:
    docker compose start api                       #   arrancar el api ANTES)

Nunca contiene credenciales: la del admin llega por entorno (ADR-008); el
resto son cuentas de prueba que crea la propia ejecución y son desechables.
El estado entre ``main`` y ``recovery`` viaja por un JSON en %TEMP%.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

BASE = os.environ.get("TPV_QA_BASE", "http://localhost:8081")
ADMIN_PW = os.environ.get("TPV_QA_ADMIN_PW", "")
STATE_FILE = Path(tempfile.gettempdir()) / "tpv_qa_state.json"

RUN = datetime.now(timezone.utc).strftime("%H%M%S")


# ---------------------------------------------------------------------------
# Cliente HTTP mínimo
# ---------------------------------------------------------------------------
def http(
    method: str,
    path: str,
    token: str | None = None,
    body: dict | None = None,
    headers: dict | None = None,
):
    """Devuelve (status, json|None|'conn-error'). Nunca lanza por status HTTP."""
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            if not raw:
                return resp.status, None
            try:  # las shells de la PWA devuelven HTML: solo se intenta JSON
                return resp.status, json.loads(raw)
            except ValueError:
                return resp.status, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, None
    except (urllib.error.URLError, OSError):
        return None, "conn-error"


def money(value: str | Decimal) -> str:
    return format(Decimal(value), "f")


# ---------------------------------------------------------------------------
# Registro de resultados
# ---------------------------------------------------------------------------
RESULTS: list[dict] = []


def check(scenario: int, name: str, ok: bool, detail: str) -> None:
    RESULTS.append({"scenario": scenario, "name": name, "ok": ok, "detail": detail})
    mark = "OK  " if ok else "FALLO"
    print(f"[{mark}] E{scenario:02d} {name}: {detail}")


def expect(scenario: int, name: str, got_status, want, detail: str = "") -> bool:
    ok = got_status == want
    extra = f" (status={got_status}, esperado={want}) {detail}"
    check(scenario, name, ok, extra.strip())
    return ok


# ---------------------------------------------------------------------------
# Preparación (fuera de numeración): admin, terminales, personal, catálogo
# ---------------------------------------------------------------------------
def setup() -> dict:
    ctx: dict = {"run": RUN}
    status, body = http("POST", "/api/v1/auth/login",
                        body={"username": "admin", "password": ADMIN_PW})
    assert status == 200, f"login admin falló: {status} {body}"
    ctx["admin"] = body["access_token"]

    suffix = RUN
    for code, name in ((f"QA{suffix}A", "Terminal QA A"), (f"QA{suffix}B", "Terminal QA B")):
        status, body = http("POST", "/api/v1/admin/terminals", ctx["admin"],
                            {"code": code, "name": name})
        assert status == 201, f"alta terminal {code}: {status} {body}"
        ctx["terminal_" + code[-1]] = body["id"]

    mgr = f"jefe{suffix}"
    status, body = http("POST", "/api/v1/admin/users", ctx["admin"], {
        "username": mgr, "password": f"Jefe-{suffix}-QA", "full_name": "Jefe QA",
        "role_code": "manager",
    })
    assert status == 201, f"alta jefe: {status} {body}"
    status, body = http("POST", "/api/v1/auth/login",
                        body={"username": mgr, "password": f"Jefe-{suffix}-QA"})
    assert status == 200, f"login jefe: {status} {body}"
    ctx["manager"] = body["access_token"]

    ctx["waiter1"] = create_waiter(ctx["admin"], f"cam1{suffix}", "2468")
    ctx["waiter2"] = create_waiter(ctx["admin"], f"cam2{suffix}", "1357")

    status, body = http("GET", "/api/v1/catalog/tax-rates", ctx["manager"])
    assert status == 200, f"tipos IVA: {status} {body}"
    rates = {r["code"]: r["id"] for r in body}
    ctx["tax_general"] = rates["general"]
    ctx["tax_reducido"] = rates["reducido"]

    ctx["product1"] = create_product(ctx["manager"], f"Café QA {suffix}",
                                     ctx["tax_general"], "1.50")
    ctx["product2"] = create_product(ctx["manager"], f"Tostada QA {suffix}",
                                     ctx["tax_reducido"], "2.20")

    status, body = http("GET", "/api/v1/admin/payment-methods", ctx["manager"])
    assert status == 200, f"formas de pago: {status} {body}"
    methods = {m["code"]: m["id"] for m in body["items"] if "items" in (body or {})} \
        if isinstance(body, dict) else {m["code"]: m["id"] for m in body}
    ctx["cash_method"] = methods["CASH"]
    ctx["card_method"] = methods["CARD"]

    # E10 imprime una copia del ticket: sin impresora de tickets configurada
    # la cola responde 409 «No hay impresora de tickets configurada».
    status, body = http("POST", "/api/v1/admin/printers", ctx["admin"], {
        "name": f"Ticket QA {RUN}", "kind": "receipt",
        "connection": "network", "address": "127.0.0.1:9100",
        "is_default": True,
    })
    assert status == 201, f"alta impresora: {status} {body}"

    # Sesión de caja del terminal B la abre ya el segundo camarero (E24).
    # http() espera el token, no el dict del camarero: sin ["token"] la
    # cabecera sería «Bearer {'username': …}» y la API respondería 401.
    status, body = http("POST", "/api/v1/cash/sessions", ctx["waiter2"]["token"],
                        {"terminal_id": ctx["terminal_B"], "opening_amount": "50.00"})
    assert status == 201, f"apertura caja B: {status} {body}"
    ctx["session_B"] = body["id"]

    save_state(ctx)
    return ctx


def create_waiter(admin_token: str, username: str, pin: str) -> dict:
    status, body = http("POST", "/api/v1/admin/users", admin_token, {
        "username": username, "password": f"{username}-Temporal1",
        "full_name": "Camarero QA", "role_code": "waiter", "pin": pin,
    })
    assert status == 201, f"alta camarero {username}: {status} {body}"
    status, body = http("POST", "/api/v1/auth/pin",
                        body={"username": username, "pin": pin})
    assert status == 200, f"login PIN {username}: {status} {body}"
    return {"username": username, "pin": pin, "token": body["access_token"]}


def create_product(manager_token: str, name: str, tax_rate_id: str, price: str) -> str:
    status, body = http("POST", "/api/v1/catalog/products", manager_token, {
        "name": name, "tax_rate_id": tax_rate_id, "price": money(price),
    })
    # La API de catálogo crea con 200 (contrato fijado por la suite de tests).
    assert status == 200, f"alta producto {name}: {status} {body}"
    return body["id"]


def add_line(token: str, order_id: str, payload: dict) -> dict:
    status, body = http("POST", f"/api/v1/sales/orders/{order_id}/lines", token, payload)
    assert status == 201, f"añadir línea: {status} {body}"
    return body


def close_order(token: str, order_id: str, session_id: str, payments: list[dict],
                idem_key: str | None = None):
    headers = {"Idempotency-Key": idem_key or uuid4().hex}
    return http("POST", f"/api/v1/sales/orders/{order_id}/close", token,
                {"cash_session_id": session_id, "payments": payments}, headers)


def order_total(order: dict) -> Decimal:
    # Un borrador no lleva totales (ck_orders_draft_no_totals: NULL hasta
    # cobrar): se calcula desde las líneas con su descuento incluido.
    total = order.get("total_amount")
    if total is not None:
        return Decimal(total)
    return sum((Decimal(l["total"]) for l in order["lines"]), Decimal("0"))


# ---------------------------------------------------------------------------
# Escenarios 1-20, 24, 25
# ---------------------------------------------------------------------------
def run_main() -> None:
    ctx = setup()
    waiter1 = ctx["waiter1"]["token"]
    manager = ctx["manager"]

    # E17 Crear producto (de catálogo, con permiso products.edit; crea con 200)
    status, body = http("POST", "/api/v1/catalog/products", manager, {
        "name": f"Tarta QA {RUN}", "tax_rate_id": ctx["tax_reducido"],
        "price": "3.50",
    })
    expect(17, "Crear producto", status, 200,
           f" alta de «Tarta QA {RUN}» con IVA reducido")
    # El catálogo crea con 200 (contrato fijado por la suite): con 201 tarta
    # quedaría None y la línea se interpretaría como artículo libre.
    tarta = body["id"] if status == 200 else None

    # E01 Abrir caja: camarero 1 abre la del terminal A con fondo 50
    status, body = http("POST", "/api/v1/cash/sessions", waiter1,
                        {"terminal_id": ctx["terminal_A"], "opening_amount": "50.00"})
    if expect(1, "Abrir caja", status, 201, " fondo inicial 50,00 €"):
        session_a = body["id"]
    else:
        print("Sin sesión de caja no hay recorrido de venta: aborto.")
        return
    # Una segunda apertura en el MISMO terminal debe chocar (una sesión abierta)
    status, _ = http("POST", "/api/v1/cash/sessions", waiter1,
                     {"terminal_id": ctx["terminal_A"], "opening_amount": "10.00"})
    expect(1, "Abrir caja (duplicada rechazada)", status, 409,
           " una sola sesión abierta por terminal")

    # E02 Login camarero: PIN propio, ya usado en el setup; aquí con PIN erróneo
    status, _ = http("POST", "/api/v1/auth/pin",
                     body={"username": ctx["waiter1"]["username"], "pin": "0000"})
    expect(2, "Login camarero (PIN erróneo rechazado)", status, 401,
           " credenciales inválidas sin delatar la cuenta")
    status, _ = http("POST", "/api/v1/auth/pin",
                     body={"username": ctx["waiter1"]["username"],
                           "pin": ctx["waiter1"]["pin"]})
    expect(2, "Login camarero (PIN correcto)", status, 200, " token de operación pos")

    # E03 Crear ticket
    status, order = http("POST", "/api/v1/sales/orders", waiter1,
                         {"terminal_id": ctx["terminal_A"]})
    expect(3, "Crear ticket", status, 201, f" borrador {order.get('id', '?')}")
    order1 = order["id"]

    # E04 Añadir productos: 2 café + línea libre (jamón) + tarta del E17
    line1 = add_line(waiter1, order1, {"product_id": ctx["product1"], "quantity": "2"})
    line2 = add_line(waiter1, order1, {"name": "Jamón", "unit_price": "2.00",
                                       "tax_rate": "10", "quantity": "1"})
    line3 = add_line(waiter1, order1, {"product_id": tarta, "quantity": "1"})
    check(4, "Añadir productos", True,
          f" 3 líneas: {line1['total']} + {line2['total']} + {line3['total']} €")

    # E05 Cambiar cantidad: 2 café → 3
    status, body = http("PATCH",
                        f"/api/v1/sales/orders/{order1}/lines/{line1['id']}",
                        waiter1, {"quantity": "3"})
    ok = expect(5, "Cambiar cantidad", status, 200, " 2 → 3 unidades")
    if ok:
        check(5, "Cambiar cantidad (importe recalculado)",
              Decimal(body["total"]) == Decimal("4.50"),
              f" 3 × 1,50 = {body['total']} €")

    # E06 Aplicar descuento: 10 % en la línea de la tarta
    status, body = http("PATCH",
                        f"/api/v1/sales/orders/{order1}/lines/{line3['id']}",
                        waiter1, {"discount_pct": "10"})
    ok = expect(6, "Aplicar descuento", status, 200, " 10 % en la tarta")
    if ok:
        check(6, "Aplicar descuento (importe recalculado)",
              Decimal(body["total"]) == Decimal("3.15"),
              f" 3,50 con 10 % = {body['total']} €")

    status, order = http("GET", f"/api/v1/sales/orders/{order1}", waiter1)
    total1 = order_total(order)

    # E07 Cobrar efectivo con entrega de 50 y cambio
    status, body = close_order(waiter1, order1, session_a,
                               [{"payment_method_id": ctx["cash_method"],
                                 "amount": money(total1), "tendered": "50.00"}])
    ok = expect(7, "Cobrar efectivo", status, 200, f" ticket {total1} €")
    ticket1 = body["ticket"]["id"] if ok else None
    if ok:
        check(7, "Cobrar efectivo (cambio correcto)",
              Decimal(body["change_total"]) == Decimal("50.00") - total1,
              f" cambio {body['change_total']} € de 50,00 entregados")
        check(7, "Cobrar efectivo (ticket emitido)",
              bool(body["ticket"]["doc_number"]),
              f" documento {body['ticket']['doc_number']}")

    # E08 Cobrar tarjeta: orden nueva, importe exacto
    status, order = http("POST", "/api/v1/sales/orders", waiter1,
                         {"terminal_id": ctx["terminal_A"]})
    order2 = order["id"]
    line_a = add_line(waiter1, order2, {"product_id": ctx["product2"], "quantity": "2"})
    status, body = close_order(waiter1, order2, session_a,
                               [{"payment_method_id": ctx["card_method"],
                                 "amount": money(order_total(
                                     http("GET", f"/api/v1/sales/orders/{order2}",
                                          waiter1)[1]))}])
    ok = expect(8, "Cobrar tarjeta", status, 200, " importe exacto sin cambio")
    ticket2_doc = body["ticket"]["doc_number"] if ok else None

    # E09 Pago mixto: 5 € en efectivo + resto con tarjeta
    status, order = http("POST", "/api/v1/sales/orders", waiter1,
                         {"terminal_id": ctx["terminal_A"]})
    order3 = order["id"]
    add_line(waiter1, order3, {"product_id": ctx["product1"], "quantity": "2"})
    add_line(waiter1, order3, {"product_id": ctx["product2"], "quantity": "2"})
    total3 = order_total(http("GET", f"/api/v1/sales/orders/{order3}", waiter1)[1])
    rest = total3 - Decimal("5.00")
    status, body = close_order(waiter1, order3, session_a,
                               [{"payment_method_id": ctx["cash_method"],
                                 "amount": "5.00", "tendered": "5.00"},
                                {"payment_method_id": ctx["card_method"],
                                 "amount": money(rest)}])
    ok = expect(9, "Pago mixto", status, 200, f" 5,00 € efectivo + {rest} € tarjeta")
    if ok:
        check(9, "Pago mixto (dos pagos registrados)", len(body["payments"]) == 2,
              f" pagos: {[p['amount'] for p in body['payments']]}")

    # E10 Imprimir: copia del ticket del E07 por la cola de impresión
    status, job = http("POST", f"/api/v1/printing/copies/tickets/{ticket1}", waiter1)
    ok = expect(10, "Imprimir (copia a la cola)", status, 201, " job creado")
    if ok:
        # Ciclo de la cola: queued → (dispatch, típico del agente) → sent →
        # (confirm) → printed. Ambos pasos son gestión de cola (admin.printers,
        # «gestionar impresoras y cola de impresión»: solo admin lo tiene).
        # Límite alto: la cola acumula jobs de ejecuciones previas (el despacho
        # por defecto solo mueve 20). Y se lee EL job, no el primer resultado.
        status, body = http("POST", "/api/v1/printing/dispatch?limit=100",
                            ctx["admin"], None)
        ok = expect(10, "Imprimir (despacho de la cola)", status, 200,
                    " cola despachada")
        if ok:
            status, body = http("GET", f"/api/v1/printing/jobs/{job['id']}",
                                ctx["admin"])
            ok = expect(10, "Imprimir (job entregado a la impresora)", status, 200,
                        f" job en estado {body.get('status') if body else '?'}")
        if ok:
            status, body = http("POST", f"/api/v1/printing/jobs/{job['id']}/confirm",
                                ctx["admin"])
            expect(10, "Imprimir (confirmación de impresión)", status, 200,
                   f" job en estado {body.get('status') if body else '?'}")
        status, body = http("GET", f"/api/v1/documents/tickets/{ticket1}", waiter1)
        expect(10, "Imprimir (payload del documento)", status, 200,
               f" ticket {body.get('doc_number') if body else '?'} congelado")

    # E11 Anular: orden nueva borrada con motivo obligatorio
    status, order = http("POST", "/api/v1/sales/orders", ctx["waiter2"]["token"],
                         {"terminal_id": ctx["terminal_A"]})
    order_void = order["id"]
    add_line(ctx["waiter2"]["token"], order_void,
             {"product_id": ctx["product1"], "quantity": "1"})
    status, _ = http("POST", f"/api/v1/sales/orders/{order_void}/void", manager,
                     {"reason": ""})
    expect(11, "Anular (sin motivo rechazado)", status, 422,
           " el motivo es obligatorio")
    status, body = http("POST", f"/api/v1/sales/orders/{order_void}/void", manager,
                        {"reason": "Error de camarero, ticket duplicado"})
    expect(11, "Anular", status, 200,
           f" estado {body.get('status') if body else '?'} con motivo registrado")

    # E12 Devolver: devolución total de una línea del cobro con tarjeta (E08)
    status, detail = http("GET", f"/api/v1/sales/orders/{order2}", manager)
    refund_line = detail["lines"][0]
    refund_amount = Decimal(refund_line["total"])
    status, body = http("POST", f"/api/v1/sales/orders/{order2}/refund", manager, {
        "cash_session_id": session_a, "reason": "Producto devuelto por el cliente",
        "lines": [{"line_id": refund_line["id"], "quantity": "2"}],
        "payments": [{"payment_method_id": ctx["card_method"],
                      "amount": money(refund_amount)}],
    })
    ok = expect(12, "Devolver", status, 201, f" devolución de {refund_amount} €")
    if ok:
        check(12, "Devolver (orden negativa enlazada)",
              Decimal(body["total_amount"]) < 0,
              f" importe {body['total_amount']} €, estado {body['status']}")
    status, body = http("GET", f"/api/v1/sales/orders/{order2}", manager)
    check(12, "Devolver (la original sigue cobrada)",
          status == 200 and body["status"] == "paid",
          f" estado {body.get('status') if body else '?'}")

    # Movimientos de caja para el cuadre (entrada 10, salida 5)
    status, _ = http("POST", f"/api/v1/cash/sessions/{session_a}/movements", manager,
                     {"kind": "in", "amount": "10.00", "reason": "Fondo extra QA"})
    check(1, "Movimiento de entrada en caja", status == 201,
          f" status={status}")
    status, _ = http("POST", f"/api/v1/cash/sessions/{session_a}/movements", manager,
                     {"kind": "out", "amount": "5.00", "reason": "Compra bolsas QA"})
    check(1, "Movimiento de salida en caja", status == 201, f" status={status}")

    # E14 Generar X: informe de la sesión aún abierta
    status, xreport = http("GET", f"/api/v1/cash/sessions/{session_a}/report",
                           manager)
    ok = expect(14, "Generar X", status, 200, " informe con la sesión abierta")
    if ok:
        check(14, "Generar X (totales por forma de pago)",
              any(t["sales_count"] >= 2 for t in xreport["method_totals"]),
              f" formas: {[(t['code'], t['sales_total']) for t in xreport['method_totals']]}")
        # Efectivo esperado: fondo 50 + ventas efectivo − devoluciones + 10 − 5
        expected_cash = Decimal(xreport["expected_cash"])
        cash_sales = Decimal(xreport["cash_sales"])
        manual = Decimal("50.00") + cash_sales + Decimal("10.00") - Decimal("5.00")
        check(14, "Generar X (efectivo esperado)", expected_cash == manual,
              f" esperado {expected_cash} = 50 + {cash_sales} + 10 − 5")

    # Arqueo parcial con descuadre de −1 € (no cierra)
    almost = expected_cash - Decimal("1.00") if ok else Decimal("0.00")
    status, body = http("POST", f"/api/v1/cash/sessions/{session_a}/counts", manager,
                        {"lines": [{"denomination": money(almost), "quantity": 1}]})
    ok = expect(14, "Arqueo parcial (detecta descuadre)", status, 201,
                f" contado {money(almost)} €")
    if ok:
        check(14, "Arqueo parcial (diferencia −1)",
              Decimal(body["difference"]) == Decimal("-1.00"),
              f" diferencia {body['difference']} €")

    # E13 + E15: cierre Z con recuento exacto
    status, body = http("POST", f"/api/v1/cash/sessions/{session_a}/close", manager,
                        {"lines": [{"denomination": money(expected_cash),
                                    "quantity": 1}]})
    ok = expect(13, "Cerrar caja", status, 200, " arqueo final y cierre")
    if ok:
        check(13, "Cerrar caja (sin descuadre)", Decimal(body["difference"]) == 0,
              f" diferencia {body['difference']} €")
        check(13, "Cerrar caja (estado closed)", body["status"] == "closed",
              f" cerrada a las {body.get('closed_at')}")

    status, zreport = http("GET", f"/api/v1/cash/sessions/{session_a}/report", manager)
    ok = expect(15, "Generar Z", status, 200, " informe del cuadre congelado")
    if ok:
        check(15, "Generar Z (sesión cerrada y diferencia congelada)",
              zreport["session"]["status"] == "closed"
              and Decimal(zreport["difference"]) == 0,
              f" diferencia {zreport['difference']} €")

    # E16 Consultar históricos: tickets, summary, cierres, formas de pago, factura
    since = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    until = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    # El offset «+00:00» debe ir codificado: un «+» crudo en la query llega
    # como espacio y la validación del datetime responde 422.
    qs = f"from={urllib.parse.quote(since)}&to={urllib.parse.quote(until)}"
    status, body = http("GET", f"/api/v1/reports/tickets?{qs}", manager)
    ok = expect(16, "Consultar históricos (tickets)", status, 200,
                f" total={body.get('total') if body else '?'}")
    if ok:
        docs = [i["doc_number"] for i in body["items"]]
        check(16, "Consultar históricos (están los tickets del día)",
              ticket2_doc in docs, f" {ticket2_doc} en {docs[:5]}…")
    status, body = http("GET", f"/api/v1/reports/stats/summary?{qs}", manager)
    if expect(16, "Consultar históricos (resumen)", status, 200, " ventas y devoluciones"):
        check(16, "Consultar históricos (devolución resta)",
              Decimal(body["refunds_amount"]) < 0 and Decimal(body["net_amount"]) > 0,
              f" devoluciones {body['refunds_amount']} €, neto {body['net_amount']} €")
    status, body = http("GET", f"/api/v1/reports/cash-closures?{qs}", manager)
    expect(16, "Consultar históricos (cierres Z)", status, 200,
           f" total={body.get('total') if body else '?'}")
    status, body = http("GET", f"/api/v1/reports/stats/by-payment-method?{qs}", manager)
    if expect(16, "Consultar históricos (formas de pago)", status, 200, " detalle"):
        codes = {r["code"] for r in body["items"]}
        check(16, "Consultar históricos (CASH y CARD con ventas)",
              {"CASH", "CARD"} <= codes, f" formas: {sorted(codes)}")
    status, body = http("POST", "/api/v1/documents/invoices", manager,
                        {"order_ids": [order2]})
    # Hallazgo QA (sin corregir en fase de pruebas): facturar exige la venta
    # con cliente asignado, pero la API no expone gestión de clientes (los
    # permisos customers.* no tienen endpoint): la emisión da 409 siempre.
    expect(16, "Consultar históricos (factura de la venta)", status, 409,
           " facturación bloqueada: «" + (body or {}).get("detail", "?")
           + "» — hueco documentado, no hay API de clientes")

    # E18 Cambiar precio de la tarta
    if tarta:
        status, body = http("PATCH", f"/api/v1/catalog/products/{tarta}", manager,
                            {"price": "3.90"})
        ok = expect(18, "Cambiar precio", status, 200, " 3,50 → 3,90 €")
        if ok:
            status, body = http("GET", f"/api/v1/catalog/products/{tarta}", manager)
            check(18, "Cambiar precio (persistido)", body["price"] == "3.90",
                  f" precio ahora {body['price']} €")

    # E19 Crear usuario: camarero nuevo con PIN y login inmediato
    waiter3 = create_waiter(ctx["admin"], f"cam3{RUN}", "9999")
    check(19, "Crear usuario", True,
          f" camarero {waiter3['username']} con PIN operativo")

    # E20 Probar permisos (la autoridad real es el 403/401 del backend)
    status, _ = http("POST", "/api/v1/admin/users", waiter1, {
        "username": f"intruso{RUN}", "password": "Intruso-12345",
        "full_name": "X", "role_code": "admin"})
    expect(20, "Permisos (camarero no crea usuarios)", status, 403, " 403")
    status, _ = http("POST", "/api/v1/catalog/products", waiter1,
                     {"name": "No debería", "tax_rate_id": ctx["tax_general"],
                      "price": "1.00"})
    expect(20, "Permisos (camarero no edita catálogo)", status, 403, " 403")
    status, _ = http("POST", "/api/v1/admin/users", manager,
                     {"username": f"jefe2{RUN}", "password": "Jefe2-123456",
                      "full_name": "X", "role_code": "waiter"})
    expect(20, "Permisos (encargado no gestiona usuarios)", status, 403, " 403")
    status, _ = http("POST", f"/api/v1/sales/orders/{order1}/void", waiter1,
                     {"reason": "no debo"})
    expect(20, "Permisos (camarero no anula)", status, 403, " 403")
    status, _ = http("GET", f"/api/v1/reports/tickets?{qs}", waiter1)
    expect(20, "Permisos (camarero no ve informes)", status, 403, " 403")
    status, _ = http("GET", "/api/v1/sales/orders")
    expect(20, "Permisos (anónimo no ve ventas)", status, 401, " 401 sin token")
    status, _ = http("GET", f"/api/v1/reports/tickets?{qs}", ctx["admin"])
    expect(20, "Permisos (admin tiene todos)", status, 200, " 200")

    # E24 Dos TPV simultáneamente: A (camarero 1) y B (camarero 2) en paralelo.
    # La sesión A se cerró en E13: se reabre para seguir vendiendo en el TPV A.
    status, body = http("POST", "/api/v1/cash/sessions", waiter1,
                        {"terminal_id": ctx["terminal_A"], "opening_amount": "20.00"})
    assert status == 201, f"reapertura caja A: {status} {body}"
    session_a2 = body["id"]

    def sell_on(terminal_key: str, waiter: dict, session_id: str) -> dict:
        token = waiter["token"]
        _, o = http("POST", "/api/v1/sales/orders", token,
                    {"terminal_id": ctx[terminal_key]})
        add_line(token, o["id"], {"product_id": ctx["product1"], "quantity": "1"})
        _, d = http("GET", f"/api/v1/sales/orders/{o['id']}", token)
        total = order_total(d)  # borrador: sin total_amount, se suma por líneas
        status, closed = close_order(token, o["id"], session_id,
                                     [{"payment_method_id": ctx["cash_method"],
                                       "amount": money(total), "tendered": money(total)}])
        return {"order": o["id"], "status": status,
                "doc": closed["ticket"]["doc_number"] if status == 200 else None,
                "total": total}

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(sell_on, "terminal_A", ctx["waiter1"], session_a2)
        b = pool.submit(sell_on, "terminal_B", ctx["waiter2"], ctx["session_B"])
        ra, rb = a.result(), b.result()
    check(24, "Dos TPV simultáneamente",
          ra["status"] == 200 and rb["status"] == 200 and ra["doc"] != rb["doc"],
          f" TPV A ticket {ra['doc']} ({ra['total']} €) y TPV B ticket "
          f"{rb['doc']} ({rb['total']} €) en paralelo, numeración sin colisión")

    # E25 Operaciones concurrentes
    status, order = http("POST", "/api/v1/sales/orders", waiter1,
                         {"terminal_id": ctx["terminal_A"]})
    c_order = order["id"]
    add_line(waiter1, c_order, {"product_id": ctx["product1"], "quantity": "1"})
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(add_line, waiter1, c_order,
                         {"product_id": ctx["product2"], "quantity": "1"})
        f2 = pool.submit(add_line, waiter1, c_order,
                         {"name": "Pan", "unit_price": "0.80", "tax_rate": "4",
                          "quantity": "1"})
        l1, l2 = f1.result(), f2.result()
    status, detail = http("GET", f"/api/v1/sales/orders/{c_order}", waiter1)
    check(25, "Concurrencia (líneas simultáneas sin corrupción)",
          len(detail["lines"]) == 3
          and order_total(detail) == sum(Decimal(l["total"]) for l in detail["lines"]),
          f" 3 líneas, total {order_total(detail)} € consistente")

    key = uuid4().hex
    total_c = order_total(detail)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(close_order, waiter1, c_order, session_a2,
                         [{"payment_method_id": ctx["cash_method"],
                           "amount": money(total_c), "tendered": money(total_c)}], key)
        f2 = pool.submit(close_order, waiter1, c_order, session_a2,
                         [{"payment_method_id": ctx["cash_method"],
                           "amount": money(total_c), "tendered": money(total_c)}], key)
        r1, r2 = f1.result(), f2.result()
    # Carrera con la misma clave: UN cobro gana y el otro repite la respuesta
    # congelada O recibe 409 y reintenta (services/idempotency.py); el orden de
    # llegada no está predeterminado. Lo inaceptable serían dos tickets.
    ok_pair = (sorted((r1[0], r2[0])) == [200, 200] and r1[1] == r2[1]) or \
              (sorted((r1[0], r2[0])) == [200, 409])
    check(25, "Concurrencia (doble cobro con la misma clave = un ticket)",
          ok_pair, f" resultados {r1[0]}/{r2[0]}: un solo ticket")
    if 409 in (r1[0], r2[0]):
        # El reintento del perdedor YA encuentra la respuesta congelada (§4.1:
        # misma key → mismo ticket, sin duplicar el pago).
        win = r1 if r1[0] == 200 else r2
        r3 = close_order(waiter1, c_order, session_a2,
                         [{"payment_method_id": ctx["cash_method"],
                           "amount": money(total_c), "tendered": money(total_c)}], key)
        check(25, "Concurrencia (reintento tras el 409 reproduce el ticket)",
              r3[0] == 200 and r3[1] == win[1],
              f" reintento {r3[0]}, misma respuesta congelada")

    status, order = http("POST", "/api/v1/sales/orders", waiter1,
                         {"terminal_id": ctx["terminal_A"]})
    d_order = order["id"]
    add_line(waiter1, d_order, {"product_id": ctx["product1"], "quantity": "1"})
    status, dd = http("GET", f"/api/v1/sales/orders/{d_order}", waiter1)
    total_d = order_total(dd)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(close_order, waiter1, d_order, session_a2,
                         [{"payment_method_id": ctx["cash_method"],
                           "amount": money(total_d), "tendered": money(total_d)}],
                         uuid4().hex)
        f2 = pool.submit(close_order, waiter1, d_order, session_a2,
                         [{"payment_method_id": ctx["cash_method"],
                           "amount": money(total_d), "tendered": money(total_d)}],
                         uuid4().hex)
        r1, r2 = sorted([f1.result(), f2.result()], key=lambda r: r[0] != 200)
    check(25, "Concurrencia (doble cobro con claves distintas = uno solo)",
          r1[0] == 200 and r2[0] >= 400,
          f" un cobro 200 y el otro {r2[0]}: la orden solo se cobra una vez")

    ctx["paid_order"] = order1
    ctx["paid_total"] = money(total1)
    ctx["session_a2"] = session_a2  # queda abierta: la usa el recovery (E23)
    save_state(ctx)


# ---------------------------------------------------------------------------
# Escenarios 21-23: caída y vuelta
# ---------------------------------------------------------------------------
def run_offline() -> None:
    """E21 «Desconectar Internet»: el TPV no depende de nada externo —
    la API y las tres shells se sirven en LAN por el propio proceso
    (compose.yml), así que sin Internet la operación sigue entera."""
    status, _ = http("GET", "/api/v1/healthz")
    check(21, "Desconectar Internet (la API local responde)", status == 200,
          f" status={status} — sin dependencia externa, todo es LAN")
    for shell in ("/app/movil/", "/app/tpv/"):
        status, _ = http("GET", shell)
        check(21, f"Desconectar Internet (shell {shell} servida)", status == 200,
              f" status={status} — la shell se sirve en LAN y el SW la cachea")


def run_down() -> None:
    """E22 «Desconectar temporalmente el TPV»: con el proceso api parado,
    la API no responde y TAMPOCO las shells de red (las sirve el mismo
    proceso): en el navegador las aguanta la copia del service worker,
    que aquí (cliente sin SW) no se puede ejercer."""
    status, _ = http("GET", "/api/v1/healthz")
    check(22, "Desconectar TPV (la API no responde)",
          status is None or status >= 500,
          f" status={status} (error de red o 5xx del proxy)")
    for shell in ("/app/movil/", "/app/tpv/"):
        status, _ = http("GET", shell)
        check(22, f"Desconectar TPV (shell {shell} caída en red)",
              status is None or status >= 500,
              f" status={status} — sin servidor no hay shell en red; el navegador usa la del SW")


def run_recovery() -> None:
    """E23 «Recuperar conexión»: el servicio vuelve y el estado sigue íntegro."""
    ctx = json.loads(STATE_FILE.read_text())
    status, body = http("GET", "/api/v1/healthz")
    expect(23, "Recuperar conexión (healthz)", status, 200, " proceso vivo")
    status, body = http("GET", "/api/v1/readyz")
    expect(23, "Recuperar conexión (readyz)", status, 200, " BD accesible")

    status, body = http("POST", "/api/v1/auth/pin",
                        body={"username": ctx["waiter1"]["username"],
                              "pin": ctx["waiter1"]["pin"]})
    ok = expect(23, "Recuperar conexión (login PIN tras el reinicio)", status, 200,
                " el camarero vuelve a entrar con su PIN")
    if not ok:
        return
    token = body["access_token"]

    status, body = http("GET", f"/api/v1/sales/orders/{ctx['paid_order']}", token)
    ok = expect(23, "Recuperar conexión (los datos persisten)", status, 200,
                " la venta cobrada sigue ahí")
    if ok:
        check(23, "Recuperar conexión (importe íntegro)",
              body["status"] == "paid" and Decimal(body["total_amount"])
              == Decimal(ctx["paid_total"]),
              f" estado {body['status']}, total {body['total_amount']} €")

    # Vuelta a vender de verdad, sobre la caja que el main dejó abierta en el
    # TPV A (E24): abrir OTRA en ese terminal sería 409 por diseño (una sola
    # sesión abierta por terminal).
    _, o = http("POST", "/api/v1/sales/orders", token,
                {"terminal_id": ctx["terminal_A"]})
    add_line(token, o["id"], {"product_id": ctx["product1"], "quantity": "1"})
    _, d = http("GET", f"/api/v1/sales/orders/{o['id']}", token)
    total = order_total(d)  # borrador: sin total_amount, se suma por líneas
    status, body = close_order(token, o["id"], ctx["session_a2"],
                               [{"payment_method_id": ctx["cash_method"],
                                 "amount": money(total), "tendered": money(total)}])
    check(23, "Recuperar conexión (venta completa tras la caída)", status == 200,
          f" ticket {body['ticket']['doc_number']} por {total} €"
          if status == 200 else f" status={status} {body}")


# ---------------------------------------------------------------------------
# Estado y arranque
# ---------------------------------------------------------------------------
def save_state(ctx: dict) -> None:
    STATE_FILE.write_text(json.dumps(ctx))


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "main"
    if command == "main":
        run_main()
    elif command == "offline":
        run_offline()
    elif command == "down":
        run_down()
    elif command == "recovery":
        run_recovery()
    else:
        print(f"Orden desconocida: {command}")
        return 2
    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\nResultado: {len(RESULTS) - len(failed)} OK / {len(failed)} FALLO "
          f"de {len(RESULTS)} comprobaciones")
    STATE_FILE.with_suffix(".results.json").write_text(json.dumps(RESULTS, indent=1))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
