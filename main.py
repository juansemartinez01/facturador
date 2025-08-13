import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Ensure app directory is on the path so generador_facturas can import bill_request
sys.path.append(str(Path(__file__).resolve().parent / "app"))

from app.generador_facturas import TokenSignManager, FacturadorWSFE

app = FastAPI(title="Facturador ARCA")


class CertPayload(BaseModel):
    cuit_emisor: int
    cert_content: str
    key_content: str
    test: bool = True


class FacturaPayload(BaseModel):
    cuit_emisor: int
    importe_total: float
    test: bool = True
    punto_venta: int = 1
    factura_tipo: int = 11
    metodo_pago: int = 1
    importe_neto: float | None = None
    importe_iva: float = 0.0
    importe_total_concepto: float = 0.0
    importe_exento: float = 0.0
    importe_tributos: float = 0.0
    alicuotas_iva: list | None = None
    doc_tipo: int = 99
    doc_nro: int = 0
    cond_iva_receptor: int = 5
    concepto: int = 1
    moneda: str = "PES"
    moneda_pago: str = "N"
    cotizacion: str = "1"
    tipo_comprobante_original: int | None = None
    pto_venta_original: int | None = None
    nro_comprobante_original: int | None = None
    cuit_receptor_comprobante_original: int | None = None

@app.post("/certificados")
def guardar_certificado(payload: CertPayload):
    manager = TokenSignManager(
        cuit_emisor=payload.cuit_emisor,
        test=payload.test,
        cert_content=payload.cert_content,
        key_content=payload.key_content,
    )
    try:
        manager.guardar_cert_db()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"detail": "Certificado almacenado"}


@app.post("/facturas")
def emitir_factura(payload: FacturaPayload):
    manager = TokenSignManager(cuit_emisor=payload.cuit_emisor, test=payload.test)
    token_info = manager.obtener_token_sign()
    if not token_info:
        raise HTTPException(status_code=500, detail="No se pudo obtener token")

    facturador = FacturadorWSFE(
        token=token_info["token"],
        sign=token_info["sign"],
        cuit_emisor=payload.cuit_emisor,
        importe_total=payload.importe_total,
        test=payload.test,
        punto_venta=payload.punto_venta,
        factura_tipo=payload.factura_tipo,
        metodo_pago=payload.metodo_pago,
        importe_neto=payload.importe_neto,
        importe_iva=payload.importe_iva,
        importe_total_concepto=payload.importe_total_concepto,
        importe_exento=payload.importe_exento,
        importe_tributos=payload.importe_tributos,
        alicuotas_iva=payload.alicuotas_iva,
        doc_tipo=payload.doc_tipo,
        doc_nro=payload.doc_nro,
        cond_iva_receptor=payload.cond_iva_receptor,
        concepto=payload.concepto,
        moneda=payload.moneda,
        moneda_pago=payload.moneda_pago,
        cotizacion=payload.cotizacion,
        tipo_comprobante_original=payload.tipo_comprobante_original,
        pto_venta_original=payload.pto_venta_original,
        nro_comprobante_original=payload.nro_comprobante_original,
        cuit_receptor_comprobante_original=payload.cuit_receptor_comprobante_original,
    )

    try:
        resultado = facturador.emitir_factura_afip()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return resultado


@app.get("/")
def read_root():
    return {"detail": "API Facturador ARCA"}