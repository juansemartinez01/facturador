import subprocess
import base64
import os
import json
import requests
import logging
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from Facturador_ARCA_Monotributo.app.bill_request import Bill_HttpRequest

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
            service : str = "wsfe"
    ):
        self.cuit_emisor = cuit_emisor
        self.test = test
        self.modo = "test" if test else "prod",
        self.ta_path = f"./app/tmp/TA-{self.cuit_emisor}-{self.modo}.xml",
        self.service = service

    def is_token_valid(self) -> bool:
        if not os.path.exists(self.ta_path):
            logger.warning(f"🔍 Archivo TA no encontrado: {self.ta_path}")
            return False
        try:
            tree = ET.parse(self.ta_path)
            root = tree.getroot()
            expiration = root.find(".//expirationTime").text
            expiration_dt = datetime.strptime(expiration[:19], "%Y-%m-%dT%H:%M:%S")
            now = datetime.now()
            is_valid = now < expiration_dt
            if is_valid:
                logger.info(f"✅ Token aún válido para {self.cuit_emisor} ({self.modo}). Expira: {expiration}")
            else:
                logger.warning(f"⏰ Token expirado para {self.cuit_emisor} ({self.modo}). Expiró: {expiration}")
            return is_valid
        except Exception as e:
            logger.error(f"❌ Error leyendo TA.xml: {e}")
            return False

    def obtener_nuevo_token_y_sign(cert_path, key_path, CUIT_emisor, service="wsfe", test=True):
        wsaa_url = "https://wsaahomo.afip.gov.ar/ws/services/LoginCms" if test else "https://wsaa.afip.gov.ar/ws/services/LoginCms"

        # === 1. Crear TRA.xml como string
        generation_time = (datetime.now() - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S")
        expiration_time = (datetime.now() + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S")
        tra_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <loginTicketRequest version="1.0">
    <header>
        <uniqueId>{int(datetime.now().timestamp())}</uniqueId>
        <generationTime>{generation_time}</generationTime>
        <expirationTime>{expiration_time}</expirationTime>
    </header>
    <service>{service}</service>
    </loginTicketRequest>
    """

        # === 2. Ejecutar openssl smime con entrada/salida en memoria
        openssl_cmd = [
            "openssl", "smime", "-sign",
            "-signer", cert_path,
            "-inkey", key_path,
            "-outform", "DER",
            "-nodetach"
        ]

        try:
            proc = subprocess.Popen(openssl_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            cms_data, stderr = proc.communicate(input=tra_xml.encode("utf-8"))

            if proc.returncode != 0:
                raise Exception(f"OpenSSL error: {stderr.decode()}")

        except Exception as e:
            logger.error("❌ Error firmando el TRA con OpenSSL:", e)
            return None

        # === 3. Codificar CMS en base64
        cms_encoded = base64.b64encode(cms_data).decode()

        # === 4. Crear request SOAP
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

        # === 5. Enviar request al WSAA
        response = requests.post(wsaa_url, data=soap_request.encode("utf-8"), headers=headers)

        if response.status_code != 200:
            logger.error("❌ Error en conexión o WSAA:")
            logger.debug(response.text[:1000])
            return None

        # === 6. Parsear TA.xml desde la respuesta
        soap_tree = ET.fromstring(response.text)
        ns = {"soap": "http://schemas.xmlsoap.org/soap/envelope/"}
        ta_xml_str = soap_tree.find(".//soap:Body/*/*", namespaces=ns).text

        ta_tree = ET.fromstring(ta_xml_str)
        token = ta_tree.find(".//token").text
        sign = ta_tree.find(".//sign").text
        expiration = ta_tree.find(".//expirationTime").text

        # === 7. Guardar TA.xml personalizado
        suffix = "-test" if test else ""
        ta_filename = f"/tmp/TA-{CUIT_emisor}{suffix}.xml"

        try:
            with open(ta_filename, "w", encoding="utf-8") as f:
                f.write(ta_xml_str)
            logger.info(f"✅ TOKEN y SIGN extraídos y guardados en {ta_filename}")
        except Exception as e:
            logger.error("⚠️ No se pudo guardar el TA.xml:", e)

        return {
            "token": token,
            "sign": sign,
            "expiration": expiration,
            "ta_xml": ta_xml_str,
            "ta_path": ta_filename
        }

    def obtener_token_sign(cert_path, key_path, CUIT_emisor, service="wsfe", test = True):
        """
        Obtener el token y sign para el servicio wsfe de AFIP.
        Si el token es válido, lo devuelve. Si no, obtiene uno nuevo.
        """
        if is_token_valid():
            logger.info("✅ TOKEN y SIGN válidos.")
            with open("TA.xml", "r") as f:
                ta_xml_str = f.read()
            ta_tree = ET.fromstring(ta_xml_str)
            token = ta_tree.find(".//token").text
            sign = ta_tree.find(".//sign").text
            expiration_time = ta_tree.find(".//expirationTime").text
            return {
                "token": token,
                "sign": sign,
                "expiration": expiration_time,
                "ta_xml": ta_xml_str
            }
        else:
            logger.info("TOKEN y SIGN vencidos. Obteniendo uno nuevo...")
            return obtener_nuevo_token_y_sign(cert_path, key_path, CUIT_emisor, service, test)


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
        fecha_cbte_dt = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))
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

    




