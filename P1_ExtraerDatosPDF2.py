import fitz  # libreria para leer y extrar texto de un pdf
import tkinter as tk  # libreria para crear interfaces graficas
from tkinter import filedialog, messagebox  # libreria para abrir cuadros de dialogo y mensajes
import re  # libreria para trabajar con expresiones regulares
from datetime import datetime  # libreria para trabajar con fechas y horas
import camelot
import pandas as pd
from pathlib import Path
import sys
import traceback
import shutil


def _primer_dir_existente(*candidatos: Path) -> str:
    for c in candidatos:
        if c.exists() and c.is_dir():
            return str(c)
    return str(Path.home())

# Clase principal para extraer datos de un PDF
class ExtractorPDF:    
    def __init__(self):
        # Esto hace que sea facil agregar nuevos campos o modificar existentes
        self.patrones = {
            # Campos de fecha y hora
            "FECHA Y HORA DE INICIO": r"FECHA\s*Y\s*HORA\s*DE\s*INICI[O0]\s*:?\s*([0-9]{4}-[0-9]{2}-[0-9]{2}\s*[0-9]{2}:[0-9]{2})",
            "FECHA Y HORA DE FINALIZACIÓN": r"FECHA\s*Y\s*HORA\s*DE\s*FINAL\w*\s*:?\s*([0-9]{4}-[0-9]{2}-[0-9]{2}\s*[0-9]{2}:[0-9]{2})",
            "FECHA PROGRAMADA": r"FECHA PROGRAMADA:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})",
            "FECHA": r"FECHA:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})",
            
            # Campos de tiempo
            "DURACIÓN ESTIMADA": r"DURACIÓN ESTIMADA:\s*([0-9]{2}:[0-9]{2}:[0-9]{2})",
            "TIEMPO DE EJECUCIÓN": r"TIEMPO DE EJECUCIÓN:\s*([0-9]{2}:[0-9]{2}:[0-9]{2})",
            "TIEMPO REAL DE PARO DEL ACTIVO": r"TIEMPO REAL DE PARO DEL ACTIVO:\s*([0-9]{2}:[0-9]{2}:[0-9]{2})",
            
            # Campos de texto
            "N°": r"N\s*[°º\*o]?\s*:?\s*OT?\s*([0-9]{3,10})",
            "NOTAS": r"NOTAS:\s*([a-zA-Z0-9 ,.:-]{0,100})",
            "DESCRIPCIÓN": r"DESCRIPCI[ÓO0Q6]N\s*:?\s*([^\n\r]{0,200})",
            "TIPO DE TAREA": r"TIPO DE TAREA:\s*([a-zA-Z0-9 ,.:-]{0,100})",
            # OJO: estos cuatro se rellenarán desde la tabla
            "DESCRIPCIÓN DE LA FALLA O SINTOMA": r"DESCRIPCIÓN DE LA FALLA O SINTOMA:\s*([a-zA-Z0-9 ,.:-]{0,200})",
            "ACCIONES REALIZADAS": r"ACCIONES REALIZADAS:\s*([a-zA-Z0-9 ,.:-]{0,200})",
            "ACCIONES PENDIENTES": r"ACCIONES PENDIENTES:\s*([a-zA-Z0-9 ,.:-]{0,200})",
            "RESPUESTOS SOLICITADOS": r"RESPUESTOS SOLICITADOS:\s*([a-zA-Z0-9 ,.:-]{0,200})",
            "OBSERVACIONES": r"OBSERVACIONES:\s*([a-zA-Z0-9 ,.:-]{0,200})",
            "REVISION DE LAS TAREAS DE BAJA FRECUENCIA": r"REVISION DE LAS TAREAS DE BAJA FRECUENCIA:\s*([a-zA-Z0-9 ,.:-]{0,200})",
            "REVISION Y SEGUIMIENTO DE LAS RECOMENDACIONES": r"REVISION Y SEGUIMIENTO DE LAS RECOMENDACIONES:\s*([a-zA-Z0-9 ,.:-]{0,200})",
            "HORAS DE FILAMENTO Y BEAM": r"HORAS DE FILAMENTO Y BEAM:\s*([a-zA-Z0-9 ,.:-]{0,200})",
            "REPUESTOS A SOLICITAR": r"REPUESTOS A SOLICITAR:\s*([a-zA-Z0-9 ,.:-]{0,200})",

        }

    def _norm_spaces(self, s: str) -> str:
        """Colapsa whitespace raro del OCR (dobles espacios, tabs, NBSP)."""
        if s is None:
            return ""
        s = s.replace("\u00A0", " ")  # NBSP
        s = re.sub(r"[ \t]+", " ", s)
        return s.strip()

    def _upper_norm(self, s: str) -> str:
        """Upper + colapsa espacios, para comparar tokens del OCR."""
        return self._norm_spaces(s).upper()

    def _clean_word(self, w: str) -> str:
        """Normaliza palabra para matching (quita puntuación rara)."""
        w = (w or "").strip()
        w = w.replace("\u00A0", " ")
        w = w.upper()
        w = re.sub(r"[^A-Z0-9ÁÉÍÓÚÑ]+", "", w)
        return w

    def _sort_words(self, words):
        # words: (x0,y0,x1,y1,word,block,line,wordno)
        return sorted(words, key=lambda t: (round(t[1], 1), round(t[0], 1)))

    def _find_phrase_bbox(self, words, phrase_tokens):
        """
        Busca una frase (lista de tokens) en words y devuelve bbox (x0,y0,x1,y1) o None.
        Matching tolerante: limpia cada palabra/tok con _clean_word.
        """
        toks = [self._clean_word(t) for t in phrase_tokens if self._clean_word(t)]
        if not toks:
            return None

        ws = self._sort_words(words)
        cleaned = [self._clean_word(w[4]) for w in ws]

        n = len(toks)
        for i in range(0, len(cleaned) - n + 1):
            if cleaned[i:i+n] == toks:
                xs0 = [ws[j][0] for j in range(i, i+n)]
                ys0 = [ws[j][1] for j in range(i, i+n)]
                xs1 = [ws[j][2] for j in range(i, i+n)]
                ys1 = [ws[j][3] for j in range(i, i+n)]
                return (min(xs0), min(ys0), max(xs1), max(ys1))
        return None

    def _collect_text_in_rect(self, words, rect, y_tol=2.5):
        """
        Junta palabras dentro de un rectángulo (x0,y0,x1,y1) preservando líneas.
        """
        x0, y0, x1, y1 = rect
        inside = []
        for w in words:
            wx0, wy0, wx1, wy1, txt, *_ = w
            if wx0 >= x0 and wx1 <= x1 and wy0 >= y0 and wy1 <= y1:
                inside.append(w)

        inside = self._sort_words(inside)
        if not inside:
            return ""

        lines = []
        current = []
        last_y = None

        for w in inside:
            y = w[1]
            t = self._norm_spaces(w[4])
            if not t:
                continue
            if last_y is None or abs(y - last_y) <= y_tol:
                current.append(t)
            else:
                lines.append(" ".join(current))
                current = [t]
            last_y = y

        if current:
            lines.append(" ".join(current))

        # Limpieza final
        lines = [self._norm_spaces(l) for l in lines if self._norm_spaces(l)]
        return "\n".join(lines).strip()

    # Extrae NOTAS desde el texto plano
    def extraer_notas_desde_texto(self, texto):
        lineas = texto.splitlines()

        labels_notas = ["NOTAS:", "NOTAS :"]
        labels_notas_norm = [self._upper_norm(x) for x in labels_notas]

        stop_tokens = [
            "DESCRIPCIÓN DE LA FALLA", "DESCRIPCION DE LA FALLA",
            "DESCRIPCIÓN:", "DESCRIPCION:",
            "TIPO DE TAREA",
            "SUBTAREAS", "SUBTAREA",
            "ACCIONES REALIZADAS",
            "ACCIONES PENDIENTES",
            "REPUESTOS SOLICITADOS", "RESPUESTOS SOLICITADOS",
            "REPUESTOS A SOLICITAR",
            "REPUESTOS", "RESPUESTOS",
            "REVISION DE LAS TAREAS DE BAJA FRECUENCIA",
            "REVISION Y SEGUIMIENTO DE LAS RECOMENDACIONES",
            "HORAS DE FILAMENTO Y BEAM",
            "OBSERVACIONES",
            "ACTIVOS",
            "INTERNATIONAL CLINICS",
            "ORDEN DE TRABAJO",
            "CALIFICACIÓN", "CALIFICACION",
            "PÁG", "PAG ", "PÁGINA", "PAGINA",
            "REALIZADO CON",
            "TODOS LOS DERECHOS RESERVADOS",
            "ISO 9001", "9001:2015",
            "N°:", "Nº:", "FECHA:",
            # ✅ agrega también campos de tiempo para que NO se mezcle con "DURACIÓN ESTIMADA"
            "DURACIÓN ESTIMADA", "DURACION ESTIMADA",
            "TIEMPO DE EJECUCIÓN", "TIEMPO DE EJECUCION",
        ]
        stop_norm = [self._upper_norm(t) for t in stop_tokens]

        max_lineas_extra = 3

        for idx, linea in enumerate(lineas):
            upper = self._upper_norm(linea)

            etiqueta_norm = None
            pos_etiqueta = -1

            for en in labels_notas_norm:
                if en in upper:
                    etiqueta_norm = en
                    pos_etiqueta = upper.find(en)   # ✅ aquí estaba el bug
                    break

            if etiqueta_norm is None:
                continue

            # lo que viene después de NOTAS: en la misma línea
            inicio_contenido = pos_etiqueta + len(etiqueta_norm)
            cola = linea[inicio_contenido:].strip(" :.-\t")
            cola = re.sub(r"^\s*NOTAS\s*:?\s*", "", cola, flags=re.IGNORECASE)

            partes = []
            if cola:
                partes.append(cola)

            # líneas siguientes (máx 3) si parecen continuación
            j = idx + 1
            extra = 0
            while j < len(lineas) and extra < max_lineas_extra:
                l = lineas[j].strip()
                if not l:
                    break

                upper_l = self._upper_norm(l)

                if any(tok in upper_l for tok in stop_norm):
                    break

                # si parece un campo nuevo "XXXX: ..."
                if re.match(r'^[A-ZÁÉÍÓÚÑ0-9 ]{2,30}:', upper_l):
                    break

                partes.append(l)
                j += 1
                extra += 1

            notas = " ".join(partes).strip()
            notas = " ".join(notas.split())
            return notas

        return ""

    # Extrae el texto completo del PDF
    def extraer_texto_pdf(self, ruta_pdf):
        try:
            documento = fitz.open(ruta_pdf)
            partes = []
            for i, pagina in enumerate(documento):
                # sort=True ordena por posición (y, luego x), MUY importante en OCR “sándwich”
                partes.append(pagina.get_text("text", sort=True))
            documento.close()
            return "\n".join(partes)
        except Exception as e:
            print(f"Error al abrir el PDF: {e}")
            return ""

    #Extrae el titulo del documento PDF
    def extraer_titulo(self, texto):
        #Primero intentar buscar el patrón específico "INTERNATIONAL CLINICS S.A."
        patron_titulo_especifico = r"INTERNATIONAL\s+CLINICS?\s+S\.?A\.?"
        coincidencia = re.search(patron_titulo_especifico, texto, re.IGNORECASE)
        
        if coincidencia:
            return coincidencia.group(0).upper()
        
        # Si no encuentra el patrón específico, buscar al principio del documento
        lineas = texto.strip().split('\n')
        lineas_no_vacias = [linea.strip() for linea in lineas if linea.strip()]
        
        if not lineas_no_vacias:
            return "No encontrado"
        
        # Buscar en las primeras líneas
        for i, linea in enumerate(lineas_no_vacias[:15]):  # Revisar las primeras 15 líneas
            # Limpiar la línea de espacios extra
            linea_limpia = ' '.join(linea.split())
            
            # Saltar líneas muy cortas o que son solo números/fechas
            if len(linea_limpia) < 5 or linea_limpia.replace('-', '').replace(':', '').replace(' ', '').isdigit():
                continue
                
            # Saltar líneas que contienen indicadores de metadatos
            indicadores_meta = ['N°:', 'FECHA:', 'PÁGINA', 'OT', 'MR_']
            if any(keyword in linea.upper() for keyword in indicadores_meta):
                continue
            
            # Buscar patrones que parezcan nombres de empresa/institución
            patrones_empresa = [
                r'^[A-Z][A-Z\s\.&,-]{10,}$',  # Todo en mayúsculas, longitud razonable
                r'.*CLINIC.*',                 # Contiene "CLINIC"
                r'.*HOSPITAL.*',               # Contiene "HOSPITAL"  
                r'.*S\.A\..*',                 # Contiene "S.A."
                r'.*LTDA.*',                   # Contiene "LTDA"
            ]
            
            for patron in patrones_empresa:
                if re.match(patron, linea_limpia, re.IGNORECASE):
                    return linea_limpia
        
        # Como último recurso, devolver la primera línea significativa
        for linea in lineas_no_vacias[:5]:
            linea_limpia = ' '.join(linea.split())
            if len(linea_limpia) > 5 and not linea_limpia.replace('-', '').replace(':', '').isdigit():
                return linea_limpia
                
        return "No encontrado"
    
    def buscar_patron(self, texto, nombre_campo):
        # buscar en el diccionario de patrones
        if nombre_campo in self.patrones:
            patron = self.patrones[nombre_campo]
            coincidencia = re.search(patron, texto, re.IGNORECASE)
            
            if coincidencia:
                return coincidencia.group(1)
        
        # Si el campo no existe
        if nombre_campo not in self.patrones:
            return "Patron no definido"
        
        # Si existe el patrón pero no se encontró coincidencia
        return "No encontrado"

    # Extrae tablas del PDF usando Camelot (debug opcional)
    def extraer_tabla_camelot(self, ruta_pdf):
        tablas = camelot.read_pdf(ruta_pdf, pages="all")
        for i, tabla in enumerate(tablas):
            print(f"Tabla {i}:")
            print(tabla.df)  # DataFrame con los datos de la tabla

    # =============== NUEVO: leer SUBTAREAS desde la(s) tabla(s) ===============
    def extraer_subtareas_desde_tabla(self, ruta_pdf):
        """
        Recorre todas las tablas del PDF (todas las páginas, lattice y stream) y arma pares
        etiqueta -> valor para la tabla de SUBTAREAS.

        - Falla, Acciones, Repuestos solicitados, Revisiones, etc. se detectan por filas
          (con continuidad entre tablas).
        - HORAS DE FILAMENTO Y BEAM, REPUESTOS A SOLICITAR y OBSERVACIONES
          se capturan solo como celdas específicas y NO se usan para continuidad,
          para evitar que arrastren texto de otras etiquetas entre páginas.
        """
        subtareas = {}

        # Tokens genéricos para detectar cada tipo de campo
        tokens_falla = ["FALLA", "FALLAS", "SINTOMA", "SÍNTOMA"]
        tokens_acc_real = ["ACCIONES REALIZADAS"]
        tokens_acc_pend = ["ACCIONES PENDIENTES"]
        tokens_repuestos = ["REPUESTOS", "RESPUESTOS"]  # genérico (pero luego excluimos 'REPUESTOS A SOLICITAR')
        tokens_obs = ["OBSERVACIONES"]

        # NUEVO: revisiones (con y sin tilde)
        tokens_revision_baja_frecuencia = [
            "REVISION DE LAS TAREAS DE BAJA FRECUENCIA",
            "REVISIÓN DE LAS TAREAS DE BAJA FRECUENCIA",
        ]
        tokens_revision_recomendaciones = [
            "REVISION Y SEGUIMIENTO DE LAS RECOMENDACIONES",
            "REVISIÓN Y SEGUIMIENTO DE LAS RECOMENDACIONES",
        ]

        tokens_horas = ["HORAS DE FILAMENTO Y BEAM"]
        tokens_rep_solicitar = ["REPUESTOS A SOLICITAR"]

        # Para detectar qué tablas parecen ser la sección de SUBTAREAS
        detectores_tabla = (
            tokens_falla
            + tokens_acc_real
            + tokens_acc_pend
            + tokens_repuestos
            + tokens_obs
            + tokens_revision_baja_frecuencia
            + tokens_revision_recomendaciones
            + tokens_horas
            + tokens_rep_solicitar
            + ["SUBTAREAS", "SUBTAREA"]
        )

        # Última etiqueta vista, para permitir continuidad entre tablas/páginas
        ultimo_label_global = None

        for flavor in ["lattice", "stream"]:
            try:
                tablas = camelot.read_pdf(ruta_pdf, pages="all", flavor=flavor)
            except Exception as e:
                print(f"Error al leer tablas con Camelot ({flavor}): {e}")
                continue

            for idx, tabla in enumerate(tablas):
                df = tabla.df

                # ¿Esta tabla contiene algo relacionado a SUBTAREAS / nuestros campos?
                if not df.applymap(
                    lambda x: any(pk in str(x).upper() for pk in detectores_tabla)
                ).any().any():
                    continue

                # ---- PRIMERA PASADA: FALLA / ACCIONES / REPUESTOS / REVISIONES ----
                current_label = ultimo_label_global

                for _, fila in df.iterrows():
                    columnas = [str(c).strip() for c in list(fila)]
                    columnas = ["" if c.lower() == "nan" else c for c in columnas]

                    if all(c == "" for c in columnas):
                        continue

                    row_upper = " | ".join(columnas).upper()

                    # Saltar cabeceras generales tipo "SUBTAREAS"
                    if "SUBTAREAS" in row_upper and not any(
                        t in row_upper
                        for t in (
                            tokens_falla
                            + tokens_acc_real
                            + tokens_acc_pend
                            + tokens_repuestos
                            + tokens_obs
                            + tokens_revision_baja_frecuencia
                            + tokens_revision_recomendaciones
                            + tokens_horas
                            + tokens_rep_solicitar
                        )
                    ):
                        continue

                    # Si la fila es "OBSERVACIONES GENERALES", cortamos continuidad y seguimos
                    if "OBSERVACIONES GENERALES" in row_upper:
                        current_label = None
                        continue

                    # Filas especiales: HORAS / REPUESTOS A SOLICITAR / OBSERVACIONES
                    # Se gestionan SOLO en la segunda pasada, no como continuidad.
                    if (
                        any(t in row_upper for t in tokens_horas)
                        or any(t in row_upper for t in tokens_rep_solicitar)
                        or ("OBSERVACIONES" in row_upper and "OBSERVACIONES GENERALES" not in row_upper)
                    ):
                        current_label = None
                        continue

                    # 1) ¿Esta fila inicia una NUEVA etiqueta genérica?
                    label_idx = None
                    for j, celda in enumerate(columnas):
                        upper = celda.upper()

                        es_falla = any(t in upper for t in tokens_falla)
                        es_acc_real = any(t in upper for t in tokens_acc_real)
                        es_acc_pend = any(t in upper for t in tokens_acc_pend)

                        # REPUESTOS genérico (REPUESTOS SOLICITADOS / RESPUESTOS SOLICITADOS),
                        # EXCLUYENDO explícitamente "REPUESTOS A SOLICITAR"
                        es_repuestos = (
                            any(t in upper for t in tokens_repuestos)
                            and "REPUESTOS A SOLICITAR" not in upper
                        )

                        # NUEVO: revisiones como etiquetas normales
                        es_rev_baja = any(t in upper for t in tokens_revision_baja_frecuencia)
                        es_rev_recom = any(t in upper for t in tokens_revision_recomendaciones)

                        if es_falla or es_acc_real or es_acc_pend or es_repuestos or es_rev_baja or es_rev_recom:
                            label_idx = j
                            break

                    if label_idx is not None:
                        etiqueta = columnas[label_idx]

                        posibles_valores = [
                            c for i, c in enumerate(columnas)
                            if i > label_idx and c
                        ]
                        valor_inicial = " ".join(posibles_valores).strip()

                        current_label = etiqueta
                        ultimo_label_global = etiqueta

                        if etiqueta not in subtareas or not subtareas[etiqueta]:
                            subtareas[etiqueta] = valor_inicial
                        else:
                            if valor_inicial:
                                subtareas[etiqueta] += " " + valor_inicial

                        continue

                    # 2) Fila sin nueva etiqueta pero hay etiqueta vigente → continuación
                    if current_label:
                        continuation_parts = [c for c in columnas if c]
                        if continuation_parts:
                            texto_cont = " ".join(continuation_parts).strip()
                            if texto_cont:
                                if subtareas.get(current_label):
                                    subtareas[current_label] += " " + texto_cont
                                else:
                                    subtareas[current_label] = texto_cont

                # ---------- SEGUNDA PASADA: CAMPOS "EXACTOS" DE CELDA ----------
                labels_exact = {
                    "HORAS DE FILAMENTO Y BEAM": "HORAS DE FILAMENTO Y BEAM",
                    "REPUESTOS A SOLICITAR": "REPUESTOS A SOLICITAR",
                    "OBSERVACIONES": "OBSERVACIONES",
                    # NUEVAS ETIQUETAS QUE VIENEN CORTADAS EN VARIAS LÍNEAS
                    "REVISION DE LAS TAREAS DE BAJA FRECUENCIA": "REVISION DE LAS TAREAS DE BAJA FRECUENCIA",
                    "REVISION Y SEGUIMIENTO DE LAS RECOMENDACIONES": "REVISION Y SEGUIMIENTO DE LAS RECOMENDACIONES",
                }

                for r in range(df.shape[0]):
                    for c in range(df.shape[1]):
                        cell = str(df.iat[r, c]).strip()
                        if not cell or cell.lower() == "nan":
                            continue

                        upper_cell = cell.upper()
                        # Normalizamos espacios y saltos de línea: "A\nB" -> "A B"
                        upper_norm = " ".join(upper_cell.split())

                        # Saltamos OBSERVACIONES GENERALES
                        if "OBSERVACIONES GENERALES" in upper_norm:
                            continue

                        for etiqueta_raw, clave_canonica in labels_exact.items():
                            # También normalizamos la clave cruda por si acaso
                            etiqueta_norm = " ".join(etiqueta_raw.split())
                            if etiqueta_norm in upper_norm:
                                # Tomamos como valor lo que está a la derecha (columnas siguientes)
                                valores = []
                                for cc in range(c + 1, df.shape[1]):
                                    val = str(df.iat[r, cc]).strip()
                                    if val and val.lower() != "nan":
                                        valores.append(val)
                                valor_celda = " ".join(valores).strip()

                                if valor_celda:
                                    subtareas[clave_canonica] = valor_celda

                                break  # dejamos de revisar más etiquetas para esta celda

            if subtareas:
                break  # si ya encontramos algo útil con este flavor, no probamos el otro

        # DEBUG: si quieres ver qué salió de aquí, descomenta esto:
        if subtareas:
            print("\n--- RESUMEN SUBTAREAS extraídas (verificación) ---")
            for k, v in subtareas.items():
                print(f"[{k}] -> {v}")

        return subtareas

    # =============== NUEVO: extraer DESCRIPCIÓN DE LA FALLA O SINTOMA desde texto plano ===============
    def extraer_descripcion_falla_desde_texto(self, texto):
        labels_falla = [
            "DESCRIPCIÓN DE LA FALLA O SINTOMA",
            "DESCRIPCION DE LA FALLA O SINTOMA",
            "DESCRIPCIÓN DE LA FALLA O SÍNTOMA",
            "DESCRIPCION DE LA FALLA O SÍNTOMA",
        ]
        labels_falla_norm = [self._upper_norm(x) for x in labels_falla]

        stop_tokens = [
            "FALLA O SINTOMA",
            "FALLA O SÍNTOMA",
            "REVISION DE LAS TAREAS DE BAJA FRECUENCIA",
            "REVISION Y SEGUIMIENTO DE LAS RECOMENDACIONES",
            "ACCIONES REALIZADAS",
            "ACCIONES PENDIENTES",
            "REPUESTOS SOLICITADOS",
            "RESPUESTOS SOLICITADOS",
            "REPUESTOS",
            "RESPUESTOS",
            "OBSERVACIONES",
            "INTERNATIONAL CLINICS",
            "ORDEN DE TRABAJO",
            "CALIFICACIÓN",
            "CALIFICACION",
            "PÁG", "PAG ", "PÁGINA", "PAGINA",
            "REALIZADO CON",
            "TODOS LOS DERECHOS RESERVADOS",
            "ISO 9001", "9001:2015",
            "N°:", "Nº:", "FECHA:",
            "N*:", "NO:", "N O:",
        ]
        stop_norm = [self._upper_norm(t) for t in stop_tokens]  # ✅ una vez

        lineas = texto.splitlines()

        for idx, linea in enumerate(lineas):
            upper = self._upper_norm(linea)

            etiqueta_norm = None
            for lf in labels_falla_norm:
                if lf in upper:
                    etiqueta_norm = lf
                    break
            if not etiqueta_norm:
                continue

            pos = upper.find(etiqueta_norm)
            inicio_contenido = pos + len(etiqueta_norm)
            cola = linea[inicio_contenido:].strip(" :.-\t")

            partes = []
            if cola:
                partes.append(cola)

            j = idx + 1
            while j < len(lineas):
                l = lineas[j].strip()
                if not l:
                    break

                upper_l = self._upper_norm(l)
                if any(tok in upper_l for tok in stop_norm):
                    break

                partes.append(l)
                j += 1

            descripcion = " ".join(partes).strip()
            descripcion = " ".join(descripcion.split())
            return descripcion

        return ""

    def extraer_subtareas_por_coordenadas(self, ruta_pdf: str) -> dict:
        """
        Extrae campos SUBTAREAS usando palabras + coordenadas.
        Mucho más robusto en PDF OCR (tipo 'sandwich') que Camelot.
        """
        targets = [
            # (clave_canonica, lista_de_variantes_de_frase)
            ("DESCRIPCIÓN DE LA FALLA O SINTOMA", [
                ["DESCRIPCIÓN", "DE", "LA", "FALLA", "O", "SINTOMA"],
                ["DESCRIPCION", "DE", "LA", "FALLA", "O", "SINTOMA"],
                ["DESCRIPCIÓN", "DE", "LA", "FALLA", "O", "SÍNTOMA"],
                ["DESCRIPCION", "DE", "LA", "FALLA", "O", "SÍNTOMA"],
            ]),
            ("ACCIONES REALIZADAS", [
                ["ACCIONES", "REALIZADAS"],
            ]),
            ("ACCIONES PENDIENTES", [
                ["ACCIONES", "PENDIENTES"],
            ]),
            ("RESPUESTOS SOLICITADOS", [
                ["RESPUESTOS", "SOLICITADOS"],
                ["REPUESTOS", "SOLICITADOS"],
            ]),
            ("REVISION DE LAS TAREAS DE BAJA FRECUENCIA", [
                ["REVISION", "DE", "LAS", "TAREAS", "DE", "BAJA", "FRECUENCIA"],
                ["REVISIÓN", "DE", "LAS", "TAREAS", "DE", "BAJA", "FRECUENCIA"],
            ]),
            ("REVISION Y SEGUIMIENTO DE LAS RECOMENDACIONES", [
                ["REVISION", "Y", "SEGUIMIENTO", "DE", "LAS", "RECOMENDACIONES"],
                ["REVISIÓN", "Y", "SEGUIMIENTO", "DE", "LAS", "RECOMENDACIONES"],
            ]),
            ("HORAS DE FILAMENTO Y BEAM", [
                ["HORAS", "DE", "FILAMENTO", "Y", "BEAM"],
            ]),
            ("REPUESTOS A SOLICITAR", [
                ["REPUESTOS", "A", "SOLICITAR"],
            ]),
            ("OBSERVACIONES", [
                ["OBSERVACIONES"],
            ]),
        ]

        out = {}
        doc = fitz.open(ruta_pdf)

        try:
            for page in doc:
                words = page.get_text("words")
                if not words:
                    continue

                w = page.rect.width
                # 1) Encontrar bboxes de todas las etiquetas (y guardarlas por Y)
                found = []
                for key, variants in targets:
                    bbox = None
                    for phrase in variants:
                        bbox = self._find_phrase_bbox(words, phrase)
                        if bbox:
                            # Caso especial OBSERVACIONES GENERALES: si justo después aparece GENERALES, ignorar
                            if key == "OBSERVACIONES":
                                # buscar en la misma línea si aparece "GENERALES"
                                y_line = bbox[1]
                                same_line = [ww for ww in words if abs(ww[1] - y_line) <= 2.5]
                                same_line = self._sort_words(same_line)
                                # words posteriores a la etiqueta
                                after = [ww for ww in same_line if ww[0] > bbox[2] - 1]
                                after_txt = " ".join(self._clean_word(ww[4]) for ww in after[:2])
                                if "GENERALES" in after_txt:
                                    bbox = None
                            if bbox:
                                found.append((key, bbox))
                                break

                if not found:
                    continue

                found.sort(key=lambda t: t[1][1])  # por y0
                # ✅ quitar duplicados (quedarse con el primero por y)
                unique = {}
                for k, bb in found:
                    if k not in unique:
                        unique[k] = bb
                found = [(k, bb) for k, bb in unique.items()]
                found.sort(key=lambda t: t[1][1])

                # 2) Para cada etiqueta, recortar hasta la siguiente etiqueta
                for i, (key, bbox) in enumerate(found):
                    x0, y0, x1, y1 = bbox
                    next_y = page.rect.height - 5
                    if i + 1 < len(found):
                        next_y = found[i+1][1][1] - 2

                    # rect de valor: a la derecha (y, si el label es largo, cae a un x razonable)
                    x_start = min(x1 + 4, w * 0.55)
                    if x_start > w * 0.85:  # label demasiado largo
                        x_start = w * 0.25

                    rect = (x_start, y0 - 1, w - 8, next_y)

                    val = self._collect_text_in_rect(words, rect)
                    val = val.strip()

                    # Si salió vacío, intentar "debajo" (algunos formularios ponen valor bajo el label)
                    if not val:
                        rect2 = (w * 0.25, y1 + 1, w - 8, next_y)
                        val = self._collect_text_in_rect(words, rect2).strip()

                    if val:
                        # Evitar contaminación: si el valor contiene otras etiquetas, cortarlo
                        cut_markers = [
                            "ACCIONES REALIZADAS", "ACCIONES PENDIENTES",
                            "RESPUESTOS SOLICITADOS", "REPUESTOS SOLICITADOS",
                            "REVISION DE LAS TAREAS", "REVISION Y SEGUIMIENTO",
                            "HORAS DE FILAMENTO", "REPUESTOS A SOLICITAR",
                            "OBSERVACIONES"
                        ]
                        val_u = self._upper_norm(val)
                        for m in cut_markers:
                            m_u = self._upper_norm(m)
                            if m_u in val_u and m_u != self._upper_norm(key):
                                pos = val_u.find(m_u)
                                if pos > 0:
                                    val = val[:pos].rstrip()
                                    break

                        val = self._norm_spaces(val.replace("\n", " \n ").replace("  ", " "))
                        val = "\n".join([self._norm_spaces(x) for x in val.splitlines() if self._norm_spaces(x)]).strip()

                        # guardar (si ya existe, concatenar)
                        if key in out and out[key]:
                            if val not in out[key]:
                                out[key] = (out[key] + "\n" + val).strip()
                        else:
                            out[key] = val

            print("\n--- SUBTAREAS COORD ---")
            for k,v in out.items():
                print(f"[{k}] -> {v}\n")

            return out
        finally:
            doc.close()

    # =============== NUEVO: extraer OBSERVACIONES desde texto plano ===============
    def extraer_observaciones_desde_texto(self, texto):
        stop_tokens = [
            "DESCRIPCIÓN DE LA FALLA",
            "DESCRIPCION DE LA FALLA",
            "FALLA O SINTOMA",
            "FALLA O SÍNTOMA",
            "ACCIONES REALIZADAS",
            "ACCIONES PENDIENTES",
            "REPUESTOS SOLICITADOS",
            "RESPUESTOS SOLICITADOS",
            "REPUESTOS",
            "RESPUESTOS",
            "OBSERVACIONES GENERALES",
            "INTERNATIONAL CLINICS",
            "ORDEN DE TRABAJO",
            "CALIFICACIÓN",
            "CALIFICACION",
            "PÁG", "PAG ", "PÁGINA", "PAGINA",
            "REALIZADO CON",
            "TODOS LOS DERECHOS RESERVADOS",
            "ISO 9001", "9001:2015",
            "N°:", "Nº:", "FECHA:"
        ]
        stop_norm = [self._upper_norm(t) for t in stop_tokens]  # ✅ una vez

        lineas = texto.splitlines()

        for idx, linea in enumerate(lineas):
            upper = self._upper_norm(linea)

            if "OBSERVACIONES" in upper and "OBSERVACIONES GENERALES" not in upper:
                pos = upper.find("OBSERVACIONES")
                inicio_contenido = pos + len("OBSERVACIONES")
                cola = linea[inicio_contenido:].strip(" :.-\t")

                partes = []
                if cola:
                    partes.append(cola)

                j = idx + 1
                while j < len(lineas):
                    l = lineas[j].strip()
                    if not l:
                        break

                    upper_l = self._upper_norm(l)
                    if any(tok in upper_l for tok in stop_norm):
                        break

                    partes.append(l)
                    j += 1

                obs = " ".join(partes).strip()
                obs = " ".join(obs.split())
                return obs

        return ""

    # =============== Mapea etiquetas de tabla a tus claves estándar ===============
    def integrar_subtareas_en_datos(self, datos, subtareas_tabla):
        """
        Mapea las etiquetas encontradas en la tabla al diccionario 'datos'
        usando las claves estándar del script.
        """
        for etiqueta, valor in subtareas_tabla.items():
            # Normalizamos un poco (quitamos saltos de línea y duplicamos espacios)
            clave = " ".join(etiqueta.upper().split())

            # 1) DESCRIPCIÓN DE LA FALLA
            if ("DESCRIPCIÓN DE LA FALLA" in clave or
                "DESCRIPCION DE LA FALLA" in clave or
                "FALLA O SINTOMA" in clave or
                "FALLA O SÍNTOMA" in clave):
                datos["DESCRIPCIÓN DE LA FALLA O SINTOMA"] = valor

            # 2) ACCIONES
            elif "ACCIONES REALIZADAS" in clave:
                datos["ACCIONES REALIZADAS"] = valor

            elif "ACCIONES PENDIENTES" in clave:
                datos["ACCIONES PENDIENTES"] = valor

            # 3) REVISIONES (con y sin tilde, aunque en la tabla venga partida en líneas)
            elif "REVISION DE LAS TAREAS" in clave or "REVISIÓN DE LAS TAREAS" in clave:
                datos["REVISION DE LAS TAREAS DE BAJA FRECUENCIA"] = valor

            elif "REVISION Y SEGUIMIENTO" in clave or "REVISIÓN Y SEGUIMIENTO" in clave:
                datos["REVISION Y SEGUIMIENTO DE LAS RECOMENDACIONES"] = valor

            # 4) HORAS DE FILAMENTO Y BEAM
            elif "HORAS DE FILAMENTO Y BEAM" in clave:
                datos["HORAS DE FILAMENTO Y BEAM"] = valor

            # 5) REPUESTOS A SOLICITAR  (campo independiente)
            elif "REPUESTOS A SOLICITAR" in clave:
                datos["REPUESTOS A SOLICITAR"] = valor

            # 6) OBSERVACIONES (pero no "OBSERVACIONES GENERALES")
            elif "OBSERVACIONES" in clave and "OBSERVACIONES GENERALES" not in clave:
                datos["OBSERVACIONES"] = valor

            # 7) REPUESTOS SOLICITADOS (solo cuando aparece explícito)
            elif ("REPUESTOS SOLICITADOS" in clave or
                  "RESPUESTOS SOLICITADOS" in clave):
                datos["RESPUESTOS SOLICITADOS"] = valor

            else:
                # Cualquier otra etiqueta se guarda tal cual por si acaso
                datos[etiqueta] = valor

        return datos

    # =============== Asegúrate que esta versión de extraer_todos_los_datos esté así ===============
    def extraer_todos_los_datos(self, texto, ruta_pdf=None):
        resultados = {}
        
        # Extraer el título primero
        resultados["TÍTULO"] = self.extraer_titulo(texto)
        
        # Extraer todos los campos definidos en los patrones
        for nombre_campo in self.patrones:
            resultados[nombre_campo] = self.buscar_patron(texto, nombre_campo)
        
        # Calcular duración registrada
        fecha_inicio_str = resultados["FECHA Y HORA DE INICIO"]
        fecha_termino_str = resultados["FECHA Y HORA DE FINALIZACIÓN"]
        formato_fecha = "%Y-%m-%d %H:%M"

        try:
            fecha_inicio = datetime.strptime(fecha_inicio_str, formato_fecha)
            fecha_termino = datetime.strptime(fecha_termino_str, formato_fecha)
            resultados["DURACION REGISTRADA"] = (fecha_termino - fecha_inicio)
        except Exception:
            resultados["DURACION REGISTRADA"] = "No calculable"

        # 1) Intentar rellenar SUBTAREAS desde las tablas
        if ruta_pdf is not None:
            subtareas_tabla = self.extraer_subtareas_desde_tabla(ruta_pdf)
            if subtareas_tabla:
                resultados = self.integrar_subtareas_en_datos(resultados, subtareas_tabla)

        # 1b) Fallback fuerte: coordenadas (para PDF escaneado / OCR)
        if ruta_pdf is not None:
            subt_coords = self.extraer_subtareas_por_coordenadas(ruta_pdf)
            if subt_coords:
                resultados = self.integrar_subtareas_en_datos(resultados, subt_coords)


        # 1.b) Fallback robusto: SUBTAREAS por coordenadas (OCR sandwich)
        keys_sub = [
            "DESCRIPCIÓN DE LA FALLA O SINTOMA",
            "ACCIONES REALIZADAS",
            "ACCIONES PENDIENTES",
            "RESPUESTOS SOLICITADOS",
            "REVISION DE LAS TAREAS DE BAJA FRECUENCIA",
            "REVISION Y SEGUIMIENTO DE LAS RECOMENDACIONES",
            "HORAS DE FILAMENTO Y BEAM",
            "REPUESTOS A SOLICITAR",
            "OBSERVACIONES",
        ]

        def _malo(v: str) -> bool:
            v = v or ""
            vu = self._upper_norm(v)
            if v in ("", "No encontrado"):
                return True
            # contaminación típica
            return any(x in vu for x in [
                "ACCIONES REALIZADAS", "ACCIONES PENDIENTES",
                "RESPUESTOS SOLICITADOS", "REPUESTOS SOLICITADOS"
            ])

        need_sub = any(_malo(resultados.get(k, "")) for k in keys_sub)


        if ruta_pdf is not None and need_sub:
            sub_coord = self.extraer_subtareas_por_coordenadas(ruta_pdf)
            if sub_coord:
                # Solo rellenar lo que falte o lo que venga contaminado
                for k, v in sub_coord.items():
                    cur = resultados.get(k, "")
                    cur_u = self._upper_norm(cur)
                    # si falta o si trae “ACCIONES…” dentro (contaminación), reemplazar
                    contaminado = any(x in cur_u for x in [
                        "ACCIONES REALIZADAS", "ACCIONES PENDIENTES",
                        "RESPUESTOS SOLICITADOS", "REPUESTOS SOLICITADOS"
                    ])
                    if (not cur) or cur == "No encontrado" or contaminado:
                        resultados[k] = v

        # 2) Fallback: DESCRIPCIÓN DE LA FALLA O SINTOMA desde texto plano
        valor_falla = resultados.get("DESCRIPCIÓN DE LA FALLA O SINTOMA", "")
        if not valor_falla or valor_falla == "No encontrado":
            desc_falla = self.extraer_descripcion_falla_desde_texto(texto)
            if desc_falla:
                resultados["DESCRIPCIÓN DE LA FALLA O SINTOMA"] = desc_falla

        # 3) Mejora para NOTAS: usar extracción multilinea controlada
        notas_texto = self.extraer_notas_desde_texto(texto)
        if notas_texto:
            resultados["NOTAS"] = notas_texto

        # 4) Fallback: OBSERVACIONES desde texto plano (si tabla o regex no la llenaron)
        valor_obs = resultados.get("OBSERVACIONES", "")
        if not valor_obs or valor_obs == "No encontrado":
            obs = self.extraer_observaciones_desde_texto(texto)
            if obs:
                resultados["OBSERVACIONES"] = obs

        # 5) Normalizar DESCRIPCIÓN: si contiene UNIQUE, dejar solo 'UNIQUE'
        desc = resultados.get("DESCRIPCIÓN", "")
        if desc and desc != "No encontrado":
            if "UNIQUE" in desc.upper():
                resultados["DESCRIPCIÓN"] = "UNIQUE"
        return resultados
    
    def agregar_patron(self, nombre_campo, patron_regex):
        self.patrones[nombre_campo] = patron_regex

    def mostrar_resultados(self, datos, ruta_pdf: str | None = None, out_dir: Path | None = None):
        from pathlib import Path
        import pandas as pd

        print("\n" + "="*50)
        print("           DATOS EXTRAÍDOS DEL PDF")
        print("="*50)

        # Crear el texto para mostrar y guardar
        texto_salida = "="*50 + "\n"
        texto_salida += "           DATOS EXTRAÍDOS DEL PDF\n"
        texto_salida += "="*50 + "\n"

        # Aquí iremos guardando las filas para Excel, en el MISMO orden que el TXT
        filas_excel = []

        # Mostrar título primero
        if "TÍTULO" in datos:
            titulo_line = f"\nTÍTULO: {datos['TÍTULO']}"
            print(titulo_line)
            texto_salida += titulo_line + "\n"
            print("-"*50)
            texto_salida += "-"*50 + "\n"

            filas_excel.append({
                "Categoría": "TÍTULO",
                "Campo": "TÍTULO",
                "Valor": str(datos["TÍTULO"])
            })

        # Agrupar por categorías para mejor lectura
        categorias = {
            "INFORMACIÓN GENERAL": ["N°", "FECHA", "FECHA PROGRAMADA"],
            "TIEMPOS": [
                "FECHA Y HORA DE INICIO",
                "FECHA Y HORA DE FINALIZACIÓN",
                "DURACIÓN ESTIMADA",
                "TIEMPO DE EJECUCIÓN",
                "TIEMPO REAL DE PARO DEL ACTIVO"
            ],
            "DETALLES": ["DESCRIPCIÓN", "TIPO DE TAREA", "NOTAS"],
            "SUBTAREAS": [
                "DESCRIPCIÓN DE LA FALLA O SINTOMA",
                "ACCIONES REALIZADAS",
                "ACCIONES PENDIENTES",
                "RESPUESTOS SOLICITADOS",
                "REVISION DE LAS TAREAS DE BAJA FRECUENCIA",
                "REVISION Y SEGUIMIENTO DE LAS RECOMENDACIONES",
                "HORAS DE FILAMENTO Y BEAM",
                "REPUESTOS A SOLICITAR",
                "OBSERVACIONES"
            ],
            "OTROS": ["DURACION REGISTRADA"]
        }

        # Mostrar categorías y llenar filas_excel
        for categoria, campos in categorias.items():
            categoria_line = f"\n{categoria}"
            print(categoria_line)
            texto_salida += categoria_line + "\n"

            for campo in campos:
                if campo in datos and datos[campo] != "No encontrado":
                    valor = datos[campo]
                    campo_line = f"  • {campo}: {valor}"
                    print(campo_line)
                    texto_salida += campo_line + "\n"

                    filas_excel.append({
                        "Categoría": categoria,
                        "Campo": campo,
                        "Valor": str(valor)
                    })

        # ========= NOMBRE BASE: OTxxxx_MPmm / OTxxxx_MCmm =========
        numero_ot = str(datos.get("N°", "")).strip()
        numero_ot = re.sub(r'^\s*OT[\s-]*', '', numero_ot, flags=re.IGNORECASE)
        tipo_tarea = str(datos.get("TIPO DE TAREA", "")).upper()
        fecha_inicio_str = str(datos.get("FECHA Y HORA DE INICIO", "")).strip()

        if "PREVENTIVA" in tipo_tarea:
            codigo_tipo = "MP"
        elif "CORRECTIVA" in tipo_tarea:
            codigo_tipo = "MC"
        else:
            codigo_tipo = "OT"

        mes = "00"
        # formato esperado: YYYY-MM-DD HH:MM
        if len(fecha_inicio_str) >= 7:
            mes = fecha_inicio_str[5:7]

        if numero_ot:
            nombre_base = f"OT{numero_ot}_{codigo_tipo}{mes}"
        else:
            nombre_base = f"OT_{codigo_tipo}{mes}"

        # ========= Preparar carpeta Resumen (si out_dir fue entregado) =========
        resumen_dir = None
        if out_dir is not None:
            out_dir = Path(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            resumen_dir = out_dir / "Resumen"
            resumen_dir.mkdir(parents=True, exist_ok=True)

        # ========= GUARDAR PDF "FORMATO P1" (solo si out_dir fue entregado) =========
        pdf_guardado = None
        if ruta_pdf and out_dir is not None:
            nombre_pdf = nombre_base + ".pdf"
            dst_pdf = out_dir / nombre_pdf

            # ✅ Si existe, preguntar sobrescritura
            if dst_pdf.exists():
                ok = messagebox.askyesno(
                    "Archivo existe",
                    f"Ya existe el PDF:\n{dst_pdf.name}\n\n¿Quieres sobrescribirlo?"
                )
                if not ok:
                    messagebox.showinfo("Cancelado", "No se guardó el PDF (no se sobrescribió).")
                    return  # cancelamos todo para no dejar TXT/Excel sin PDF

            try:
                shutil.copy2(ruta_pdf, dst_pdf)
                pdf_guardado = str(dst_pdf)
            except Exception as e:
                print(f"Error al guardar PDF en destino: {e}")

        # ========= GUARDAR TXT =========
        sugerido = nombre_base + ".txt"

        if out_dir is not None:
            # ✅ TXT dentro de Resumen
            ruta_txt = str(resumen_dir / sugerido)
        else:
            home = Path.home()
            escritorio_dir = _primer_dir_existente(home / "Escritorio", home / "Desktop")

            ruta_txt = filedialog.asksaveasfilename(
                title="Guardar datos (TXT)",
                defaultextension=".txt",
                filetypes=[("Archivos de texto", "*.txt")],
                initialfile=sugerido,
                initialdir=escritorio_dir
            )

            if not ruta_txt:
                print("\nNo se guardó el archivo de texto.")
                return

        txt_ok = False
        excel_ok = False
        ruta_excel = None

        try:
            with open(ruta_txt, "w", encoding="utf-8") as f:
                f.write(texto_salida)
            txt_ok = True
            print(f"\n¡Datos TXT guardados exitosamente en:\n  {ruta_txt}")
        except Exception as e:
            print(f"\nError al guardar el archivo de texto: {e}")

        # ========= GUARDAR / ACTUALIZAR EXCEL UNIQUE.xlsx =========
        if filas_excel and txt_ok:
            if out_dir is not None:
                # ✅ Excel dentro de Resumen
                ruta_excel = resumen_dir / "UNIQUE.xlsx"
            else:
                ruta_txt_path = Path(ruta_txt)
                ruta_excel = ruta_txt_path.with_name("UNIQUE.xlsx")

            # Nombre de la hoja = nombre_base (pero cumpliendo restricciones de Excel)
            sheet_name = nombre_base
            for ch in r'[]:*?/\\':
                sheet_name = sheet_name.replace(ch, "_")
            if len(sheet_name) > 31:
                sheet_name = sheet_name[:31]

            df = pd.DataFrame(filas_excel, columns=["Categoría", "Campo", "Valor"])

            try:
                if ruta_excel.exists():
                    with pd.ExcelWriter(
                        ruta_excel,
                        engine="openpyxl",
                        mode="a",
                        if_sheet_exists="replace"
                    ) as writer:
                        df.to_excel(writer, sheet_name=sheet_name, index=False)
                else:
                    with pd.ExcelWriter(ruta_excel, engine="openpyxl") as writer:
                        df.to_excel(writer, sheet_name=sheet_name, index=False)

                excel_ok = True
                print(f"\n¡Datos Excel guardados/actualizados en:\n  {ruta_excel}")
                print(f"Hoja escrita: {sheet_name}")
            except Exception as e:
                print(f"\nError al guardar el archivo Excel: {e}")

        # ========= MENSAJES EMERGENTES =========
        if txt_ok and excel_ok:
            mensaje = (
                "✅ ¡Exportación completada!\n\n"
                "Tus datos fueron guardados correctamente.\n\n"
                f"📄 Archivo TXT:\n{ruta_txt}\n\n"
                f"📊 Archivo Excel (UNIQUE.xlsx):\n{ruta_excel}\n\n"
            )
            if pdf_guardado:
                mensaje += f"📄 PDF guardado:\n{pdf_guardado}\n\n"
            mensaje += "Puedes cerrar esta ventana con el botón Ok."

            messagebox.showinfo("✅ Datos guardados", mensaje)

        elif txt_ok and not excel_ok:
            mensaje = (
                "⚠️ Exportación parcialmente completada\n\n"
                "El archivo TXT se guardó correctamente, pero hubo un problema al guardar el Excel.\n\n"
                f"📄 Archivo TXT:\n{ruta_txt}\n\n"
                "Revisa permisos de carpeta o si el archivo Excel está abierto."
            )
            messagebox.showwarning("⚠️ Atención", mensaje)

        else:
            mensaje = (
                "❌ Ocurrió un error al intentar guardar los datos.\n\n"
                "Ningún archivo se ha guardado correctamente.\n\n"
                "Cierra esta ventana e intenta ejecutar nuevamente el programa."
            )
            messagebox.showerror("❌ Error al guardar", mensaje)

def main():

    pdf_arg = None
    out_dir_arg = None

    if len(sys.argv) >= 2:
        pdf_arg = Path(sys.argv[1])
    if len(sys.argv) >= 3:
        out_dir_arg = Path(sys.argv[2])

    # Crear root de Tk (oculto) para que file dialogs/messagebox funcionen bien
    root = tk.Tk()
    root.withdraw()
    root.update()

    try:
        extractor = ExtractorPDF()

        # --- leer argv ---
        pdf_arg = None
        out_dir_arg = None

        if len(sys.argv) >= 2:
            pdf_arg = Path(sys.argv[1])
        if len(sys.argv) >= 3:
            out_dir_arg = Path(sys.argv[2])

        # --- elegir pdf ---
        if pdf_arg is not None and pdf_arg.exists():
            ruta_pdf = str(pdf_arg)
        else:
            home = Path.home()
            descargas_dir = _primer_dir_existente(home / "Descargas", home / "Downloads")

            print("Selecciona el archivo PDF para extraer datos...")
            ruta_pdf = filedialog.askopenfilename(
                title="Seleccione el PDF",
                filetypes=[("PDF", "*.pdf")],
                initialdir=descargas_dir
            )

            if not ruta_pdf:
                print("No se seleccionó ningún archivo.")
                return


        print("Extrayendo texto del PDF...")
        texto = extractor.extraer_texto_pdf(ruta_pdf)
        if not texto:
            messagebox.showerror("Error", "No se pudo extraer texto del PDF.")
            return

        print("Analizando el contenido...")
        datos_extraidos = extractor.extraer_todos_los_datos(texto, ruta_pdf=ruta_pdf)

        extractor.mostrar_resultados(datos_extraidos, ruta_pdf=ruta_pdf, out_dir=out_dir_arg)

    except Exception as e:
        # Si algo revienta (por ejemplo, fechas "No encontrado"), lo verás en un mensaje
        msg = f"Ocurrió un error:\n\n{e}\n\nDetalle:\n{traceback.format_exc()}"
        print(msg)
        messagebox.showerror("Error", msg)

    finally:
        root.destroy()

if __name__ == "__main__":
    main()
