import subprocess
import base64
import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

def obtener_token_y_sign(cert_path, key_path, service="wsfe", wsaa_url="https://wsaahomo.afip.gov.ar/ws/services/LoginCms"):
    """
    Desde certificado y clave privada, obtener TA.xml (token de acceso) para el servicio wsfe (web service de facturación electrónica).
    Retorna un diccionario con token, sign y expirationTime si es exitoso, o None en caso de error.
    """
    # === Paths temporales ===
    tra_path = "/tmp/TRA.xml"
    cms_path = "/tmp/TRA_firma.cms"

    # === 1. Crear el TRA.xml ===
    generation_time = (datetime.now() - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S")
    expiration_time = (datetime.now() + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S")
    tra_xml = f"""
    <?xml version="1.0" encoding="UTF-8"?>
    <loginTicketRequest version="1.0">
    <header>
        <uniqueId>{int(datetime.now().timestamp())}</uniqueId>
        <generationTime>{generation_time}</generationTime>
        <expirationTime>{expiration_time}</expirationTime>
    </header>
    <service>{service}</service>
    </loginTicketRequest>
 """
    with open(tra_path, "w") as f:
        f.write(tra_xml)

    # === 2. Firmar el TRA.xml con OpenSSL ===
    subprocess.run([
        "openssl", "smime", "-sign",
        "-signer", cert_path,
        "-inkey", key_path,
        "-in", tra_path,
        "-out", cms_path,
        "-outform", "DER",
        "-nodetach"
    ], check=True)

    # === 3. Leer CMS y codificar en base64 ===
    with open(cms_path, "rb") as f:
        cms_data = f.read()
        cms_encoded = base64.b64encode(cms_data).decode()

    # === 4. Crear el request SOAP para WSAA ===
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

    # === 5. Enviar request al WSAA ===
    response = requests.post(wsaa_url, data=soap_request.encode("utf-8"), headers=headers)

    if response.status_code != 200:
        print("❌ Error en la conexión o autenticación:")
        print(response.text[:1000])
        return None

    # === 6. Parsear respuesta y extraer TA.xml ===
    soap_tree = ET.fromstring(response.text)
    ns = {"soap": "http://schemas.xmlsoap.org/soap/envelope/"}
    login_return_encoded = soap_tree.find(".//soap:Body/*/*", namespaces=ns).text
    ta_xml_str = login_return_encoded

    # === 7. Guardar y parsear TA.xml ===
    with open("TA.xml", "w", encoding="utf-8") as f:
        f.write(ta_xml_str)

    ta_tree = ET.fromstring(ta_xml_str)
    token = ta_tree.find(".//token").text
    sign = ta_tree.find(".//sign").text
    expiration_time = ta_tree.find(".//expirationTime").text

    print("✅ TOKEN y SIGN extraídos correctamente.")
    print("Token:", token[:40] + "...")
    print("Sign :", sign[:40] + "...")
    print("TA Expiration Date:", expiration_time)

    return {
        "token": token,
        "sign": sign,
        "expiration": expiration_time,
        "ta_xml": ta_xml_str
    }