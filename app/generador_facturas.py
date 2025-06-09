import subprocess
import base64
import os
import json
import requests
import logging
import sys
import tempfile
import xml.etree.ElementTree as ET
from pytz import timezone

from sqlalchemy import create_engine, text
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from bill_request import Bill_HttpRequest

logger = logging.getLogger("facturador_afip")
logger.setLevel(logging.DEBUG)

if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

class TokenSignManager:
    def __init__(
            self,
            cuit_emisor : int,
            test : bool = True,
            service : str = "wsfe",
            POSTGRES_URL: str = "postgresql://afip_user:afip_pass@localhost:5432/afip_db", # Crear variable de entorno en vez de que este aca.
            cert_content: str = None, # Abrir .crt en bloq de notas y copiar el texto.
            key_content: str = None,
    ):
        self.cuit_emisor = cuit_emisor
        self.test = test
        self.modo = "test" if test else "prod"
        self.ta_path = f"./app/tmp/TA-{self.cuit_emisor}-{self.modo}.xml"
        self.service = service
        self.POSTGRES_URL = POSTGRES_URL
        self.cert_content = cert_content
        self.key_content = key_content

    def guardar_cert_db(self):
        """
        Guarda en la base de datos el contenido del certificado (.crt) y la clave privada (.pem)
        recibidos como strings, asociados al CUIT y modo (test/prod). Si ya existe, los actualiza y renueva created_at.
        """
        POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://afip_user:afip_pass@localhost:5432/afip_db")

        try:
            modo = "test" if self.test else "prod"
            now = datetime.now()

            engine = create_engine(POSTGRES_URL)
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO certs (cuit_emisor, modo, cert, private_key, created_at)
                    VALUES (:cuit_emisor, :modo, :cert, :private_key, :created_at)
                    ON CONFLICT (cuit_emisor, modo) DO UPDATE
                    SET cert = EXCLUDED.cert,
                        private_key = EXCLUDED.private_key,
                        created_at = EXCLUDED.created_at
                """), {
                    "cuit_emisor": self.cuit_emisor,
                    "modo": modo,
                    "cert": self.cert_content,
                    "private_key": self.key_content,
                    "created_at": now
                })

            logger.info("✅ Certificado y clave guardados correctamente en la base de datos.")

        except Exception as e:
            logger.exception("❌ Error al guardar en DB")


    def is_token_valid(self) -> bool:
        try:
            # Conexión a la base de datos PostgreSQL
            engine = create_engine(self.POSTGRES_URL) # create_engine(os.getenv("POSTGRES_URL"))

            with engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT expiration_time
                    FROM ta_info
                    WHERE cuit_emisor = :cuit_emisor AND modo = :modo
                    ORDER BY expiration_time DESC
                    LIMIT 1
                """), {
                    "cuit_emisor": self.cuit_emisor,
                    "modo": self.modo
                })

                row = result.fetchone()
                if not row:
                    logger.warning(f"🔍 No se encontró registro de TA para {self.cuit_emisor} ({self.modo}) en DB.")
                    return False

                expiration_time = row[0]
                now = datetime.now()
                if now < expiration_time:
                    logger.info(f"✅ Token válido en DB para {self.cuit_emisor} ({self.modo}). Expira: {expiration_time}")
                    return True
                else:
                    logger.warning(f"⏰ Token expirado en DB para {self.cuit_emisor} ({self.modo}). Expiró: {expiration_time}")
                    return False

        except Exception as e:
            logger.error(f"❌ Error consultando token desde DB: {e}")
            return False
        
    def obtener_nuevo_token_y_sign(self):
        wsaa_url = (
            "https://wsaahomo.afip.gov.ar/ws/services/LoginCms"
            if self.test else
            "https://wsaa.afip.gov.ar/ws/services/LoginCms"
        )
        modo = "test" if self.test else "prod"

        # === 1. Leer certificado y clave desde DB
        try:
            engine = create_engine(os.getenv("POSTGRES_URL", self.POSTGRES_URL))
            with engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT cert, private_key FROM certs
                    WHERE cuit_emisor = :cuit_emisor AND modo = :modo
                    LIMIT 1
                """), {
                    "cuit_emisor": self.cuit_emisor,
                    "modo": modo
                })
                row = result.fetchone()
                if not row:
                    logger.error(f"❌ No se encontraron certificados en DB para {self.cuit_emisor} ({modo})")
                    return None
                cert_content, key_content = row
        except Exception as e:
            logger.error(f"❌ Error al leer certificados desde DB: {e}")
            return None

        # === 2. Crear archivos temporales en app/tmp
        tmp_dir = "app/tmp"
        try:
            os.makedirs(tmp_dir, exist_ok=True)
            logger.debug(f"📁 Carpeta temporal asegurada: {tmp_dir}")
        except Exception as e:
            logger.error(f"❌ No se pudo crear carpeta temporal {tmp_dir}: {e}")
            return None

        cert_path = os.path.join(tmp_dir, f"cert_{self.cuit_emisor}_{modo}.crt")
        key_path = os.path.join(tmp_dir, f"key_{self.cuit_emisor}_{modo}.pem")

        try:
            with open(cert_path, "w") as f:
                f.write(cert_content)
            with open(key_path, "w") as f:
                f.write(key_content)
        except Exception as e:
            logger.error("❌ No se pudo escribir archivos temporales de certificado:", exc_info=e)
            return None

        try:
            # === 3. Crear TRA.xml
            generation_dt = datetime.now()
            generation_time = (generation_dt - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S")
            expiration_dt = (generation_dt + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S")
            tra_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <loginTicketRequest version="1.0">
    <header>
        <uniqueId>{int(generation_dt.timestamp())}</uniqueId>
        <generationTime>{generation_time}</generationTime>
        <expirationTime>{expiration_dt}</expirationTime>
    </header>
    <service>{self.service}</service>
    </loginTicketRequest>
    """

            # === 4. Firmar el TRA
            openssl_cmd = [
                "openssl", "smime", "-sign",
                "-signer", cert_path,
                "-inkey", key_path,
                "-outform", "DER",
                "-nodetach"
            ]
            proc = subprocess.Popen(openssl_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            cms_data, stderr = proc.communicate(input=tra_xml.encode("utf-8"))
            if proc.returncode != 0:
                raise Exception(f"OpenSSL error: {stderr.decode()}")

            # === 5. Armar SOAP
            cms_encoded = base64.b64encode(cms_data).decode()
            soap_request = f"""<?xml version="1.0" encoding="UTF-8"?>
    <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                    xmlns:ser="http://wsaa.view.sua.dvadac.desein.afip.gov">
    <soapenv:Header/>
    <soapenv:Body>
        <ser:loginCms>
        <request>{cms_encoded}</request>
        </ser:loginCms>
    </soapenv:Body>
    </soapenv:Envelope>
    """
            headers = {
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": "loginCms"
            }

            # === 6. Enviar solicitud
            response = requests.post(wsaa_url, data=soap_request.encode("utf-8"), headers=headers)
            if response.status_code != 200:
                logger.error(f"❌ Error en conexión o WSAA. Código HTTP: {response.status_code}")
                logger.debug("🧾 Respuesta completa del WSAA:")
                logger.debug(response.text)
                return None

            # === 7. Parsear respuesta
            soap_tree = ET.fromstring(response.text)
            ns = {"soap": "http://schemas.xmlsoap.org/soap/envelope/"}
            ta_xml_str = soap_tree.find(".//soap:Body/*/*", namespaces=ns).text
            ta_tree = ET.fromstring(ta_xml_str)
            token = ta_tree.find(".//token").text
            sign = ta_tree.find(".//sign").text
            expiration_time_str = ta_tree.find(".//expirationTime").text
            expiration_time = datetime.strptime(expiration_time_str[:19], "%Y-%m-%dT%H:%M:%S")

            # === 8. Insertar o actualizar en PostgreSQL
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO ta_info (cuit_emisor, modo, token, sign, expiration_time, generation_time)
                    VALUES (:cuit_emisor, :modo, :token, :sign, :expiration_time, :generation_time)
                    ON CONFLICT (cuit_emisor, modo)
                    DO UPDATE SET
                        token = EXCLUDED.token,
                        sign = EXCLUDED.sign,
                        expiration_time = EXCLUDED.expiration_time,
                        generation_time = EXCLUDED.generation_time
                """), {
                    "cuit_emisor": self.cuit_emisor,
                    "modo": modo,
                    "token": token,
                    "sign": sign,
                    "expiration_time": expiration_time,
                    "generation_time": generation_dt
                })
            logger.info(f"✅ TOKEN y SIGN guardados en DB para CUIT {self.cuit_emisor}")

            return {
                "token": token,
                "sign": sign,
                "expiration": expiration_time,
                "ta_xml": ta_xml_str
            }

        except Exception as e:
            logger.error("❌ Error procesando WSAA:", exc_info=e)
            return None

        finally:
            # === 9. Eliminar archivos temporales
            try:
                os.remove(cert_path)
                os.remove(key_path)
                logger.debug(f"🧹 Archivos temporales eliminados: {cert_path}, {key_path}")
            except Exception as cleanup_error:
                logger.warning(f"⚠️ No se pudieron eliminar archivos temporales: {cleanup_error}")

    def obtener_token_sign(self):
        """
        Obtener el token y sign para el servicio wsfe de AFIP.
        Si el token es válido, lo recupera desde la base de datos.
        Si no es válido o no existe, obtiene uno nuevo y lo guarda.
        """
        if self.is_token_valid():
            try:
                engine = create_engine(self.POSTGRES_URL)
                with engine.connect() as conn:
                    result = conn.execute(text("""
                        SELECT token, sign, expiration_time
                        FROM ta_info
                        WHERE cuit_emisor = :cuit_emisor AND modo = :modo
                        ORDER BY expiration_time DESC
                        LIMIT 1
                    """), {
                        "cuit_emisor": self.cuit_emisor,
                        "modo": "test" if self.test else "prod"
                    }) 
                    row = result.fetchone()
                    if row:
                        logger.info(f"✅ TOKEN y SIGN recuperados de DB para CUIT {self.cuit_emisor}")
                        return {
                            "token": row[0],
                            "sign": row[1],
                            "expiration": row[2],
                        }
                    else:
                        logger.warning("❌ Token válido, pero no se encontró en la base de datos.")
                        return self.obtener_nuevo_token_y_sign()
            except Exception as e:
                logger.error("❌ Error recuperando token/sign de la base de datos:", exc_info=e)
                return self.obtener_nuevo_token_y_sign()
        else:
            logger.info("🔁 Token inválido o expirado. Solicitando uno nuevo...")
            return self.obtener_nuevo_token_y_sign()


    


################################################################################################
################################################################################################
################################################################################################
class FacturadorMonotributista:
    def __init__(
            self,
            token,
            sign,
            cuit_emisor : int,
            importe_total : float,
            test : bool = True,
            punto_venta : int = 1,
            factura_tipo : int = 11,
            metodo_pago : int = 1,
    ):
        self.test = test
        self.cuit_emisor = cuit_emisor
        self.importe_total = importe_total
        self.importe_neto = importe_total
        self.punto_venta = punto_venta
        self.factura_tipo = factura_tipo
        self.metodo_pago = metodo_pago
        self.token = token
        self.sign = sign

    def consultar_ultimo_numero_autorizado(self):
        soap_request = f"""<?xml version="1.0" encoding="UTF-8"?>
        <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
            xmlns:ar="http://ar.gov.afip.dif.FEV1/">
        <soapenv:Header/>
        <soapenv:Body>
            <ar:FECompUltimoAutorizado>
            <ar:Auth>
                <ar:Token>{self.token}</ar:Token>
                <ar:Sign>{self.sign}</ar:Sign>
                <ar:Cuit>{self.cuit_emisor}</ar:Cuit>
            </ar:Auth>
            <ar:PtoVta>{self.punto_venta}</ar:PtoVta>
            <ar:CbteTipo>{self.factura_tipo}</ar:CbteTipo>
            </ar:FECompUltimoAutorizado>
        </soapenv:Body>
        </soapenv:Envelope>
        """

        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "http://ar.gov.afip.dif.FEV1/FECompUltimoAutorizado"
        }

        url = "https://wswhomo.afip.gov.ar/wsfev1/service.asmx" if self.test else "https://servicios1.afip.gov.ar/wsfev1/service.asmx"
        response = requests.post(url, data=soap_request.encode("utf-8"), headers=headers)

        if response.status_code != 200:
            raise Exception(f"Error HTTP {response.status_code}: {response.text[:500]}")

        ns = {"soap": "http://schemas.xmlsoap.org/soap/envelope/", "ar": "http://ar.gov.afip.dif.FEV1/"}
        tree = ET.fromstring(response.text)

        # Verificar errores devueltos por AFIP
        error_nodo = tree.find(".//ar:Errors/ar:Err", namespaces=ns)
        if error_nodo is not None:
            code = error_nodo.find("ar:Code", namespaces=ns).text
            msg = error_nodo.find("ar:Msg", namespaces=ns).text
            raise Exception(f"AFIP Error {code}: {msg}")

        # Extraer número de comprobante
        nodo = tree.find(".//ar:FECompUltimoAutorizadoResult/ar:CbteNro", namespaces=ns)
        if nodo is None:
            raise Exception("No se pudo obtener el número de comprobante autorizado.")

        return {
            "pto_vta": self.punto_venta,
            "cbte_tipo": self.factura_tipo,
            "nro_autorizado": int(nodo.text)
        }
    
    def emitir_factura_afip(self):

        # === 1. Preparar datos de la factura ===
        ultimo = self.consultar_ultimo_numero_autorizado()
        cbte_nro_anterior = ultimo["nro_autorizado"]
        logger.info("🧾 Último comprobante emitido:", cbte_nro_anterior)

        cbte_nro_nuevo = cbte_nro_anterior + 1
        fecha_cbte_dt = datetime.now(timezone("America/Argentina/Buenos_Aires"))
        fecha_cbte = fecha_cbte_dt.strftime("%Y%m%d")

        importe_neto = self.importe_total
        # === 2. Crear SOAP request para FECAESolicitar ===
        bill = Bill_HttpRequest(
            CUIT_emisor=self.cuit_emisor,
            token=self.token,
            sign=self.sign,
            punto_venta=self.punto_venta,
            cantidad_comprobantes=1,
            metodo_pago=self.metodo_pago,
            importe_total=self.importe_total,
            importe_neto=importe_neto,
            importe_total_concepto=0,
            nro_comprobante=cbte_nro_nuevo,
            fecha_comprobante=fecha_cbte
        )

        soap_request = bill.get_request()
        logger.debug(f"📦 SOAP Request:\n{soap_request}")

        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "http://ar.gov.afip.dif.FEV1/FECAESolicitar"
        }

        url = "https://wswhomo.afip.gov.ar/wsfev1/service.asmx" if self.test else "https://servicios1.afip.gov.ar/wsfev1/service.asmx"
        response = requests.post(url, data=soap_request.encode("utf-8"), headers=headers)

        if response.status_code != 200:
            raise Exception(f"❌ Error HTTP {response.status_code}: {response.text[:500]}")

        # === 3. Parsear respuesta ===
        ns = {
            "soap": "http://schemas.xmlsoap.org/soap/envelope/",
            "ar": "http://ar.gov.afip.dif.FEV1/"
        }

        tree = ET.fromstring(response.text)

        # 🔍 Verificar errores de AFIP
        error_nodo = tree.find(".//ar:Errors/ar:Err", namespaces=ns)
        if error_nodo is not None:
            code = error_nodo.find("ar:Code", namespaces=ns).text
            msg = error_nodo.find("ar:Msg", namespaces=ns).text
            raise Exception(f"❌ AFIP Error {code}: {msg}")

        result_node = tree.find(".//ar:FECAEDetResponse", namespaces=ns)
        if result_node is None:
            raise Exception("❌ No se pudo encontrar el nodo 'FECAEDetResponse'. Respuesta completa:\n" + response.text)

        resultado = result_node.find("ar:Resultado", namespaces=ns).text

        if resultado == "A":
            cae = result_node.find("ar:CAE", namespaces=ns).text
            cae_vto = result_node.find("ar:CAEFchVto", namespaces=ns).text

            qr_data = {
                "ver": 1,
                "fecha": fecha_cbte_dt.strftime("%Y-%m-%d"),
                "cuit": int(self.cuit_emisor),
                "ptoVta": self.punto_venta,
                "tipoCmp": self.factura_tipo,
                "nroCmp": cbte_nro_nuevo,
                "importe": self.importe_total,
                "moneda": "PES",
                "ctz": 1.0,
                "tipoDocRec": 99,
                "nroDocRec": 0,
                "tipoCodAut": "E",
                "codAut": int(cae)
            }

            json_qr = base64.urlsafe_b64encode(json.dumps(qr_data).encode()).decode()
            url_qr = f"https://www.afip.gob.ar/fe/qr/?p={json_qr}"

            # Desde front o back del sistema tomar esta salida y guardarla en una DB.
            return {
                "cae": cae,
                "vencimiento": cae_vto,
                "nro_comprobante": cbte_nro_nuevo,
                "fecha": fecha_cbte_dt.strftime("%Y-%m-%d"),
                "qr_url": url_qr
            }
 
        else:
            raise Exception("❌ Error al generar factura. Resultado: " + resultado + "\nRespuesta completa:\n" + response.text)
    

################################################################################################
################################################################################################
################################################################################################

    




