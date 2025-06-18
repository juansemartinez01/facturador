from datetime import datetime

"""
Esta clase define diferentes formatos de http request dependiendo los diferentes tipos de comprobantes solicitados.
"""
class Bill_HttpRequest:
        def __init__(
                self,
                # Datos Emisor
                CUIT_emisor : int,
                token : str,
                sign : str,
                punto_venta : int,
                importe_total_concepto: float, # Importe de operaciones no gravadas ni exentas, pero no sujetas a IVA (por ejemplo, intereses, multas).
                cantidad_comprobantes: int = 1,
                metodo_pago : int = 1, # 1 = Efectivo, 2 = Tarjeta de credito, 3 = Tarjeta de debito, 4 = Cheque, 5 = Transferencia bancaria
                moneda_pago : str = "N", # "N" = Pesos, "S" = Dolares
                # Datos Receptor
                doc_tipo : int = 99, # 80 = CUIT, 96 = DNI, 99 = Consumidor Final
                doc_nro : int = 0, # Si es consumidor final puede ser "0"
                # Datos Factura 
                factura_tipo : int = 11, # FA=1 NotaDeb-A=2 NotaCred-A=3 FB=6 ND-B=7 NC-B=8 FC=11 ND-C=12 NC-C=13               
                nro_comprobante : int =0,
                fecha_comprobante: str = datetime.now().strftime("%Y%m%d"),
                moneda : str = "PES", #  "DOL"
                cotizacion : str = "1", # Si es "DOL", cotizacion actual
                concepto : int = 1, # 1 = Productos, 2 = Servicios, 3 = Ambos
                importe_total: float = 1000, # Sumatoria de todo 
                importe_neto: float = 1000, # Productos sin IVA/ Precio de los productos
                importe_exento: float = 0, # Solo si hay productos exentos de IVA
                importe_iva: float = 0,  # importe_neto * alicuota de iva
                importe_tributos: float = 0, # Total de otros tributos, como IIBB, tasas municipales
                alicuotas_iva: list = None,
                cond_iva_receptor: int = 5, # 1	IVA Responsable Inscripto (A/M/C),6	Responsable Monotributo (A/M/C),13	Monotributista Social (A/M/C),16	Monotributo Trabajador Independiente Promovido,4	IVA Sujeto Exento (B/C),5	Consumidor Final (B/C),7	Sujeto No Categorizado (B/C),8	Proveedor del Exterior (B/C),9	Cliente del Exterior (B/C),10	IVA Liberado – Ley 19.640 (B/C),15	IVA No Alcanzado (B/C)
                
                # Nota de credito/debito
                tipo_comprobante_original: int = 1,
                pto_venta_original: int = 0,
                nro_comprobante_original: int = 0,
                cuit_receptor_comprobante_original: int = 0,
                ):
                # Emisor
                self.CUIT_emisor = CUIT_emisor
                self.token = token
                self.sign = sign
                self.punto_venta = punto_venta
                self.cantidad_comprobantes = cantidad_comprobantes
                self.metodo_pago = metodo_pago

                # Receptor
                self.doc_tipo = doc_tipo
                self.doc_nro = doc_nro
                self.cond_iva_receptor = cond_iva_receptor

                # Factura
                self.factura_tipo = factura_tipo
                self.nro_comprobante = nro_comprobante
                self.fecha_comprobante = fecha_comprobante
                self.moneda = moneda
                self.moneda_pago = moneda_pago
                self.cotizacion = cotizacion
                self.concepto = concepto
                self.importe_total = importe_total
                self.importe_total_concepto = importe_total_concepto
                self.importe_neto = importe_neto
                self.importe_exento = importe_exento
                self.importe_iva = importe_iva
                self.importe_tributos = importe_tributos
                self.alicuotas_iva = alicuotas_iva or []

                # Limite consumidor final
                if doc_tipo == 99 and metodo_pago == 1 and importe_total >= 208644:
                        raise ValueError("❌ No se puede facturar a un consumidor final más de $208.644 ARS en efectivo.")
                if doc_tipo == 99 and metodo_pago in [2,3,4,5] and importe_total >= 417288:
                        raise ValueError("❌ No se puede facturar a un consumidor final más de $417.288 ARS con medios electrónicos.")

                # Comprobante original (Notas de Crédito/Débito)
                if factura_tipo not in [1, 6, 11]:  # No es factura A/B/C
                        self.tipo_comprobante_original = tipo_comprobante_original
                        self.pto_venta_original = pto_venta_original
                        self.nro_comprobante_original = nro_comprobante_original
                        self.cuit_receptor_comprobante_original = cuit_receptor_comprobante_original
                else:
                        self.tipo_comprobante_original = None
                        self.pto_venta_original = None
                        self.nro_comprobante_original = None
                        self.cuit_receptor_comprobante_original = None
        
        def get_request(self):
            cbtes_asoc_xml = ""
            if self.tipo_comprobante_original is not None:
                cbtes_asoc_xml = f"""
                                <ar:CbtesAsoc>
                                <ar:CbteAsoc>
                                        <ar:Tipo>{self.tipo_comprobante_original}</ar:Tipo>
                                        <ar:PtoVta>{self.pto_venta_original}</ar:PtoVta>
                                        <ar:Nro>{self.nro_comprobante_original}</ar:Nro>
                                        <ar:Cuit>{self.cuit_receptor_comprobante_original}</ar:Cuit>
                                </ar:CbteAsoc>
                                </ar:CbtesAsoc>"""

            iva_xml = ""
            if self.alicuotas_iva:
                alicuotas = []
                for a in self.alicuotas_iva:
                    alicuotas.append(f"<ar:AlicIva><ar:Id>{a['Id']}</ar:Id><ar:BaseImp>{a['BaseImp']}</ar:BaseImp><ar:Importe>{a['Importe']}</ar:Importe></ar:AlicIva>")
                iva_xml = f"<ar:Iva>{''.join(alicuotas)}</ar:Iva>"

            soap_request = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
                        <soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\" xmlns:ar=\"http://ar.gov.afip.dif.FEV1/\">
                        <soapenv:Header/>
                        <soapenv:Body>
                                <ar:FECAESolicitar>
                                        <ar:Auth>
                                                <ar:Token>{self.token}</ar:Token>
                                                <ar:Sign>{self.sign}</ar:Sign>
                                                <ar:Cuit>{self.CUIT_emisor}</ar:Cuit>
                                        </ar:Auth>
                                        <ar:FeCAEReq>
                                                <ar:FeCabReq>
                                                        <ar:CantReg>{self.cantidad_comprobantes}</ar:CantReg>
                                                        <ar:PtoVta>{self.punto_venta}</ar:PtoVta>
                                                        <ar:CbteTipo>{self.factura_tipo}</ar:CbteTipo>
                                                </ar:FeCabReq>
                                                <ar:FeDetReq>
                                                <ar:FECAEDetRequest>
                                                        <ar:Concepto>{self.concepto}</ar:Concepto>
                                                        <ar:DocTipo>{self.doc_tipo}</ar:DocTipo>
                                                        <ar:DocNro>{self.doc_nro}</ar:DocNro>
                                                        <ar:CbteDesde>{self.nro_comprobante}</ar:CbteDesde>
                                                        <ar:CbteHasta>{self.nro_comprobante}</ar:CbteHasta>
                                                        <ar:CbteFch>{self.fecha_comprobante}</ar:CbteFch>
                                                        <ar:ImpTotal>{self.importe_total}</ar:ImpTotal>
                                                        <ar:ImpTotConc>{self.importe_total_concepto}</ar:ImpTotConc>
                                                        <ar:ImpNeto>{self.importe_neto}</ar:ImpNeto>
                                                        <ar:ImpOpEx>{self.importe_exento}</ar:ImpOpEx>
                                                        <ar:ImpIVA>{self.importe_iva}</ar:ImpIVA>
                                                        <ar:ImpTrib>{self.importe_tributos}</ar:ImpTrib>
                                                        <ar:FchServDesde></ar:FchServDesde>
                                                        <ar:FchServHasta></ar:FchServHasta>
                                                        <ar:FchVtoPago></ar:FchVtoPago>
                                                        <ar:MonId>{self.moneda}</ar:MonId>
                                                        <ar:MonCotiz>{self.cotizacion}</ar:MonCotiz>
                                                        <ar:CondicionIVAReceptorId>{self.cond_iva_receptor}</ar:CondicionIVAReceptorId>
                                                        <ar:CanMismoMonedaExt>{self.moneda_pago}</ar:CanMismoMonedaExt>
                                                        {cbtes_asoc_xml}
                                                        {iva_xml}
                                                </ar:FECAEDetRequest>
                                                </ar:FeDetReq>
                                        </ar:FeCAEReq>
                                </ar:FECAESolicitar>
                        </soapenv:Body>
                        </soapenv:Envelope>"""
            return soap_request
