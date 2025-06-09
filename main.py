import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Ensure app directory is on the path so generador_facturas can import bill_request
sys.path.append(str(Path(__file__).resolve().parent / "app"))

from app.generador_facturas import TokenSignManager, FacturadorMonotributista

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

    facturador = FacturadorMonotributista(
        token=token_info["token"],
        sign=token_info["sign"],
        cuit_emisor=payload.cuit_emisor,
        importe_total=payload.importe_total,
        test=payload.test,
        punto_venta=payload.punto_venta,
        factura_tipo=payload.factura_tipo,
        metodo_pago=payload.metodo_pago,
    )

    try:
        resultado = facturador.emitir_factura_afip()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return resultado


@app.get("/")
def read_root():
    return {"detail": "API Facturador ARCA"}