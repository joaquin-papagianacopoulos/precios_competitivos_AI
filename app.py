from flask import Flask, render_template, request, jsonify, send_from_directory, session, flash, redirect, url_for
import pandas as pd
import os
from werkzeug.utils import secure_filename
import json
from datetime import datetime
import re
import mysql.connector
#import pymysql.cursors
from functools import wraps
from flask import redirect, url_for, session, request 
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
import urllib.parse
from flask import send_file
import regex as re


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrapper

def role_required(role):
    def decorator(f):
        @wraps(f)
        def wrapper(*a, **kw):
            if "user" not in session:
                return redirect(url_for("login"))
            if session.get("role") != role:
                # si no es del rol requerido, mandalo a su panel de usuario
                return redirect(url_for("index_usuario"))
            return f(*a, **kw)
        return wrapper
    return decorator




app = Flask(__name__)
app.secret_key = "tios"  
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# === PDFS ===
app.config['PDFS_FOLDER'] = 'pdfs'

for folder in [app.config['UPLOAD_FOLDER'], app.config.get('PDFS_FOLDER', 'pdfs')]:
    if not os.path.exists(folder):
        os.makedirs(folder)


# Crear carpeta de uploads si no existe
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Almacenamiento global para las listas de precios
price_lists = {}


# === ESTADO CARRITO / NEGOCIO ===
business_data = {}  # datos del comercio por usuario


# Ubicaciones por defecto de mayoristas
DEFAULT_LOCATIONS = {
    'Mayorista Central': 'Av. Corrientes 1234, Buenos Aires, Argentina',
    'Distribuidora Norte': 'San Martin 567, Cordoba, Argentina',
    'Comercial Sur': 'Pellegrini 890, Rosario, Argentina',
    'Proveedor Express': 'Florida 456, Buenos Aires, Argentina'
}

# === TELeFONOS PARA WHATSAPP (dummy/ejemplo) ===
SUPPLIER_PHONES = {
    'Gallesur': '+541156649404',
    'Distribuidora Norte': '+5491123456790',
    'Comercial Sur': '+5491123456791',
    'Proveedor Express': '+5491123456792'
}


class PriceListProcessor:
    def __init__(self):
        self.possible_product_columns = [
            'producto',           
            'nombre del producto',
            'descripcion', 
            'item', 
            'nombre', 
            'description', 
            'product', 
            'nombre del articulo', 
            'articulo',
            'descripción',
            'detalle'
        ]
        
        self.possible_price_columns = [
            'precio',            
            'price',
            'precio unitario', 
            'costo', 
            'valor', 
            'cost', 
            'amount', 
            'importe c/iva', 
            'importe', 
            'efectivo', 
            'unitario', 
            'pcio', 
            'prcio', 
            'precio unit', 
            'p.unit', 
            'pu', 
            'precio u', 
            'lista', 
            'precio lista', 
            'tarifa', 
            'neto'
        ]
        
        self.columns_to_ignore = [
            'código del producto', 
            'codigo del producto', 
            'código', 
            'codigo', 
            'sku', 
            'id', 
            'ref', 
            'referencia', 
            'code', 
            'item code', 
            'product code', 
            'codigo producto', 
            'código producto',
            'código artículo',    
            'codigo articulo',
            'rubro',              
            'categoria',
            'tipo'
        ]
        
        self.debug_log = []
        self.debug_log = []  # Para almacenar logs detallados
    
    def log_debug(self, message, level="INFO"):
        """Agregar mensaje al log de debug con timestamp"""
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        log_entry = f"[{timestamp}] {level}: {message}"
        print(log_entry)
        self.debug_log.append(log_entry)
    
    def get_debug_summary(self):
        """Retorna resumen del debugging"""
        return {
            'total_log_entries': len(self.debug_log),
            'recent_logs': self.debug_log[-20:],  # Últimos 20 logs
            'full_log': self.debug_log
        }
    
    def should_ignore_column(self, column_name):
        """Verifica si una columna debe ser ignorada"""
        if pd.isna(column_name):
            return False
        
        column_lower = str(column_name).lower().strip()
        
        for ignore_pattern in self.columns_to_ignore:
            if ignore_pattern in column_lower:
                self.log_debug(f"🚫 COLUMNA IGNORADA: '{column_name}' coincide con patrón '{ignore_pattern}'")
                return True
        
        return False
    
    def analyze_excel_structure(self, file_path):
        """Analiza la estructura del Excel antes de procesarlo"""
        self.log_debug(f"🔍 ANALIZANDO ESTRUCTURA: {file_path}")
        
        try:
            excel_file = pd.ExcelFile(file_path)
            structure_info = {
                'sheets': [],
                'total_sheets': len(excel_file.sheet_names),
                'file_size_mb': os.path.getsize(file_path) / (1024*1024)
            }
            
            for sheet_name in excel_file.sheet_names:
                try:
                    # Leer primeras 10 filas para análisis rápido
                    df_sample = pd.read_excel(excel_file, sheet_name=sheet_name, nrows=10, header=None)
                    
                    sheet_info = {
                        'name': sheet_name,
                        'sample_rows': df_sample.shape[0],
                        'sample_cols': df_sample.shape[1],
                        'first_5_rows': [],
                        'potential_headers': [],
                        'ignored_columns': []
                    }
                    
                    # Capturar primeras 5 filas como strings
                    for i in range(min(5, len(df_sample))):
                        row_data = [str(val) if pd.notna(val) else '' for val in df_sample.iloc[i].values]
                        sheet_info['first_5_rows'].append(row_data)
                    
                    # Buscar potenciales headers en las primeras filas
                    for row_idx in range(min(5, len(df_sample))):
                        row_values = [str(val).lower().strip() if pd.notna(val) else '' for val in df_sample.iloc[row_idx].values]
                        potential_headers = []
                        ignored_in_row = []
                        
                        for col_idx, cell_value in enumerate(row_values):
                            if cell_value:
                                # Verificar si debe ser ignorada
                                if self.should_ignore_column(cell_value):
                                    ignored_in_row.append(f"Col{col_idx}:IGNORADA({cell_value})")
                                    continue
                                
                                # Verificar si parece columna de producto
                                for prod_col in self.possible_product_columns:
                                    if prod_col in cell_value:
                                        potential_headers.append(f"Col{col_idx}:PRODUCTO({prod_col})")
                                        break
                                
                                # Verificar si parece columna de precio
                                for price_col in self.possible_price_columns:
                                    if price_col in cell_value:
                                        potential_headers.append(f"Col{col_idx}:PRECIO({price_col})")
                                        break
                        
                        if potential_headers or ignored_in_row:
                            sheet_info['potential_headers'].append({
                                'row': row_idx,
                                'headers': potential_headers,
                                'ignored': ignored_in_row
                            })
                    
                    structure_info['sheets'].append(sheet_info)
                    self.log_debug(f"📋 Hoja '{sheet_name}': {df_sample.shape[0]}x{df_sample.shape[1]} (muestra)")
                    
                except Exception as e:
                    self.log_debug(f"❌ Error analizando hoja '{sheet_name}': {e}", "ERROR")
                    structure_info['sheets'].append({
                        'name': sheet_name,
                        'error': str(e)
                    })
            
            excel_file.close()
            return structure_info
            
        except Exception as e:
            self.log_debug(f"💥 ERROR CRÍTICO analizando estructura: {e}", "ERROR")
            return {'error': str(e)}
    
    def find_column_index(self, df, possible_names):
        """Encuentra el índice de la columna con debugging detallado y filtrado de columnas ignoradas"""
        columns_lower = [str(col).lower().strip() for col in df.columns]
        
        self.log_debug(f"🔍 BUSCANDO COLUMNAS en headers: {columns_lower}")
        self.log_debug(f"🎯 Buscando coincidencias para: {possible_names}")
        
        # Primero verificar columnas a ignorar
        ignored_columns = []
        for i, col in enumerate(df.columns):
            if self.should_ignore_column(col):
                ignored_columns.append((i, col))
        
        if ignored_columns:
            self.log_debug(f"🚫 COLUMNAS IGNORADAS: {ignored_columns}")
        
        # Coincidencia exacta primero (excluyendo ignoradas)
        for name in possible_names:
            if name in columns_lower:
                idx = columns_lower.index(name)
                # Verificar si esta columna debe ser ignorada
                if not self.should_ignore_column(df.columns[idx]):
                    self.log_debug(f"✅ COINCIDENCIA EXACTA: '{name}' en columna {idx} ('{df.columns[idx]}')")
                    return idx
                else:
                    self.log_debug(f"🚫 COINCIDENCIA EXACTA IGNORADA: '{name}' en columna {idx} ('{df.columns[idx]}')")
        
        # Luego contención (excluyendo ignoradas)
        for name in possible_names:
            for i, col in enumerate(columns_lower):
                if name in col and not self.should_ignore_column(df.columns[i]):
                    self.log_debug(f"✅ COINCIDENCIA PARCIAL: '{name}' en '{col}' (columna {i})")
                    return i
                elif name in col and self.should_ignore_column(df.columns[i]):
                    self.log_debug(f"🚫 COINCIDENCIA PARCIAL IGNORADA: '{name}' en '{col}' (columna {i})")
        
        self.log_debug(f"❌ NO SE ENCONTRÓ columna para: {possible_names}", "WARNING")
        return None
    
    def find_column_in_first_rows(self, df, possible_names, max_rows=8):
        """Busca columnas en las primeras filas con debugging detallado y filtrado de ignoradas"""
        self.log_debug(f"🔍 BÚSQUEDA EN PRIMERAS {max_rows} FILAS para: {possible_names}")
        
        search_rows = min(max_rows, len(df))
        
        for row_idx in range(search_rows):
            row_values = [str(val).lower().strip() if pd.notna(val) else '' for val in df.iloc[row_idx].values]
            
            self.log_debug(f"📋 Fila {row_idx}: {row_values[:5]}...")  # Solo primeros 5 valores
            
            found_columns = {}
            ignored_in_row = []
            
            for col_idx, cell_value in enumerate(row_values):
                if cell_value:
                    # Verificar si debe ser ignorada
                    if self.should_ignore_column(cell_value):
                        ignored_in_row.append((col_idx, cell_value))
                        continue
                    
                    # Buscar coincidencias
                    for name in possible_names:
                        if name in cell_value:
                            found_columns[col_idx] = (cell_value, name)
                            self.log_debug(f"🎯 ENCONTRADO '{name}' en fila {row_idx}, columna {col_idx}: '{cell_value}'")
            
            if ignored_in_row:
                self.log_debug(f"🚫 IGNORADAS en fila {row_idx}: {ignored_in_row}")
            
            if found_columns:
                return row_idx, found_columns
        
        self.log_debug(f"❌ NO encontrado en primeras {search_rows} filas", "WARNING")
        return None, {}
    
    def clean_price(self, price_str):
        """Limpia precios con debugging detallado"""
        if pd.isna(price_str):
            return None
        
        original = str(price_str).strip()
        
        # Debug para valores problemáticos
        debug_this = len(self.debug_log) < 50 or any(x in original.lower() for x in ['error', 'n/a', '#'])
        
        if debug_this:
            self.log_debug(f"🧹 LIMPIANDO precio: '{original}' (tipo: {type(price_str)})")
        
        # Validaciones con debug
        if re.search(r'[a-zA-Z]', original):
            if debug_this:
                self.log_debug(f"❌ RECHAZADO por letras: '{original}'")
            return None
        
        if 'X' in original.upper():
            if debug_this:
                self.log_debug(f"❌ RECHAZADO por 'X': '{original}'")
            return None
        
        if re.match(r'^\d{1,2}$', original) and float(original) < 5:
            if debug_this:
                self.log_debug(f"❌ RECHAZADO por ser muy pequeño: '{original}'")
            return None
        
        if not re.search(r'\d', original):
            if debug_this:
                self.log_debug(f"❌ RECHAZADO por no tener dígitos: '{original}'")
            return None
        
        # Limpieza
        cleaned = re.sub(r'^[\$€£¥₹\s]+', '', original)
        cleaned = re.sub(r'[^\d.,]', '', cleaned)
        
        if not cleaned or cleaned in ['.', ',', '.,', ',.']:
            if debug_this:
                self.log_debug(f"❌ RECHAZADO después de limpiar: '{original}' → '{cleaned}'")
            return None
        
        try:
            # Manejo de formato argentino
            if ',' in cleaned and '.' in cleaned:
                cleaned = cleaned.replace('.', '').replace(',', '.')
            elif ',' in cleaned and cleaned.count(',') == 1:
                parts = cleaned.split(',')
                if len(parts[1]) <= 2:
                    cleaned = cleaned.replace(',', '.')
            
            price_value = float(cleaned)
            
            if price_value <= 0.01 or price_value > 999999:
                if debug_this:
                    self.log_debug(f"❌ RECHAZADO por rango: '{original}' → {price_value}")
                return None
            
            if debug_this:
                self.log_debug(f"✅ ACEPTADO: '{original}' → {price_value}")
            
            return price_value
            
        except (ValueError, TypeError) as e:
            if debug_this:
                self.log_debug(f"❌ ERROR conversión: '{original}' → '{cleaned}' - {e}")
            return None
    
    def process_excel_file(self, file_path, supplier_name):
        """Procesa Excel con debugging exhaustivo"""
        self.debug_log = []  # Reset log
        self.log_debug(f"🚀 INICIANDO procesamiento: {supplier_name} - {file_path}")
        self.log_debug(f"🚫 COLUMNAS A IGNORAR: {self.columns_to_ignore}")
        
        # Análisis previo de estructura
        structure = self.analyze_excel_structure(file_path)
        self.log_debug(f"📊 ESTRUCTURA ANALIZADA: {structure.get('total_sheets', 0)} hojas")
        
        excel_file = None
        
        try:
            excel_file = pd.ExcelFile(file_path)
            all_products = []
            sheet_summaries = []
            
            for sheet_name in excel_file.sheet_names:
                self.log_debug(f"📋 PROCESANDO HOJA: {sheet_name}")
                sheet_summary = self.process_sheet(excel_file, sheet_name, supplier_name)
                sheet_summaries.append(sheet_summary)
                
                if sheet_summary.get('products'):
                    all_products.extend(sheet_summary['products'])
                    self.log_debug(f"✅ HOJA EXITOSA: {len(sheet_summary['products'])} productos de '{sheet_name}'")
                else:
                    self.log_debug(f"❌ HOJA FALLIDA: '{sheet_name}' - {sheet_summary.get('error', 'Sin productos')}")
            
            # Resumen final
            self.log_debug(f"🎉 RESUMEN FINAL: {len(all_products)} productos de {len(excel_file.sheet_names)} hojas")
            
            debug_summary = {
                'supplier': supplier_name,
                'total_products': len(all_products),
                'sheets_processed': len(sheet_summaries),
                'sheets_successful': len([s for s in sheet_summaries if s.get('products')]),
                'sheet_details': sheet_summaries,
                'structure_analysis': structure,
                'columns_ignored': self.columns_to_ignore,
                'debug_logs': self.get_debug_summary()
            }
            
            return all_products, debug_summary
            
        except Exception as e:
            error_msg = f"💥 ERROR CRÍTICO procesando {file_path}: {str(e)}"
            self.log_debug(error_msg, "ERROR")
            return [], {'error': error_msg, 'debug_logs': self.get_debug_summary()}
        finally:
            if excel_file:
                try:
                    excel_file.close()
                except:
                    pass
    def find_best_header_row(self, df):
        """
        Encuentra la mejor fila para usar como headers analizando las primeras filas
        Retorna un diccionario con información de la mejor fila encontrada
        """
        try:
            if df.empty:
                return None
            
            max_rows_to_check = min(10, len(df))
            best_analysis = None
            best_score = 0
            
            for row_idx in range(max_rows_to_check):
                analysis = self.analyze_header_row(df, row_idx)
                
                if analysis and analysis.get('score', 0) > best_score:
                    best_score = analysis['score']
                    best_analysis = analysis
                    best_analysis['row_idx'] = row_idx
            
            return best_analysis
            
        except Exception as e:
            self.log_debug(f"Error en find_best_header_row: {e}", "ERROR")
            return None

    def analyze_header_row(self, df, row_idx):
        """
        Analiza una fila específica para determinar si es buena como header
        """
        try:
            if row_idx >= len(df):
                return None
            
            row_values = [str(val).lower().strip() if pd.notna(val) else '' for val in df.iloc[row_idx].values]
            
            product_matches = []
            price_matches = []
            ignored_columns = []
            score = 0
            
            for col_idx, cell_value in enumerate(row_values):
                if not cell_value:
                    continue
                    
                # Verificar si debe ser ignorada
                if self.should_ignore_column(cell_value):
                    ignored_columns.append((col_idx, cell_value))
                    continue
                
                # Buscar coincidencias de producto
                for prod_pattern in self.possible_product_columns:
                    if prod_pattern.lower() in cell_value:
                        product_matches.append((col_idx, cell_value, prod_pattern))
                        score += 10
                        break
                
                # Buscar coincidencias de precio
                for price_pattern in self.possible_price_columns:
                    if price_pattern.lower() in cell_value:
                        price_matches.append((col_idx, cell_value, price_pattern))
                        score += 10
                        break
            
            # Penalizar si no encontramos al menos una columna de cada tipo
            if not product_matches:
                score -= 20
            if not price_matches:
                score -= 20
                
            # Penalizar si encontramos la misma columna para producto y precio
            if product_matches and price_matches:
                product_cols = {match[0] for match in product_matches}
                price_cols = {match[0] for match in price_matches}
                if product_cols.intersection(price_cols):
                    score -= 30
            
            return {
                'row_idx': row_idx,
                'score': score,
                'product_matches': product_matches,
                'price_matches': price_matches,
                'ignored_columns': ignored_columns,
                'total_matches': len(product_matches) + len(price_matches)
            }
            
        except Exception as e:
            self.log_debug(f"Error analizando fila {row_idx}: {e}", "ERROR")
            return None
    def process_sheet(self, excel_file, sheet_name, supplier_name):
        """Procesa una hoja individual con análisis inteligente de headers"""
        try:
            self.log_debug(f"📄 Iniciando hoja: {sheet_name}")
            
            # Leer sin header para análisis completo
            df = pd.read_excel(excel_file, sheet_name=sheet_name, header=None)
            
            if df.empty:
                return {'sheet': sheet_name, 'error': 'Hoja vacía', 'products': []}
            
            self.log_debug(f"📐 Dimensiones: {df.shape[0]} filas x {df.shape[1]} columnas")
            
            # Buscar la mejor fila de headers
            best_header_analysis = self.find_best_header_row(df)
            
            if best_header_analysis is None:
                # Fallback: intentar con headers tradicionales
                self.log_debug("⚠️ Fallback: intentando headers tradicionales")
                df_with_header = pd.read_excel(excel_file, sheet_name=sheet_name)
                product_col_idx = self.find_column_index(df_with_header, self.possible_product_columns)
                price_col_idx = self.find_column_index(df_with_header, self.possible_price_columns)
                header_row = 0
            else:
                # Usar la mejor fila de headers encontrada
                header_row = best_header_analysis['row_idx']
                
                # Extraer índices de columnas
                if best_header_analysis['product_matches']:
                    product_col_idx = best_header_analysis['product_matches'][0][0]  # Primera coincidencia
                    self.log_debug(f"✅ COLUMNA PRODUCTO: {product_col_idx} ('{best_header_analysis['product_matches'][0][1]}')")
                else:
                    product_col_idx = None
                
                if best_header_analysis['price_matches']:
                    price_col_idx = best_header_analysis['price_matches'][0][0]  # Primera coincidencia
                    self.log_debug(f"✅ COLUMNA PRECIO: {price_col_idx} ('{best_header_analysis['price_matches'][0][1]}')")
                else:
                    price_col_idx = None
            
            # Validaciones críticas
            if product_col_idx is None:
                content_analysis = self.analyze_content_structure(df)
                error = f"No se encontró columna de producto. Análisis: {content_analysis}"
                self.log_debug(f"❌ {error}", "ERROR")
                return {'sheet': sheet_name, 'error': error, 'products': []}
            
            if price_col_idx is None:
                content_analysis = self.analyze_content_structure(df)
                error = f"No se encontró columna de precio. Análisis: {content_analysis}"
                self.log_debug(f"❌ {error}", "ERROR")
                return {'sheet': sheet_name, 'error': error, 'products': []}
            
            if product_col_idx == price_col_idx:
                error = f"La misma columna ({product_col_idx}) detectada para producto Y precio"
                self.log_debug(f"❌ CRÍTICO: {error}", "ERROR")
                deeper_analysis = self.deep_column_analysis(df, product_col_idx)
                error += f". Análisis profundo: {deeper_analysis}"
                return {'sheet': sheet_name, 'error': error, 'products': []}
            
            # Procesar datos desde la fila correcta
            self.log_debug(f"📋 USANDO HEADER desde fila: {header_row}")
            
            if header_row > 0:
                # Leer desde la fila de headers + 1 para los datos
                df_processed = pd.read_excel(excel_file, sheet_name=sheet_name, header=header_row)
                
                # Verificar que las columnas aún existan
                if product_col_idx >= len(df_processed.columns) or price_col_idx >= len(df_processed.columns):
                    # Si las columnas están fuera de rango, usar índices numéricos
                    self.log_debug(f"⚠️ Usando índices numéricos: producto={product_col_idx}, precio={price_col_idx}")
                    # Leer datos como matriz sin headers
                    df_data = pd.read_excel(excel_file, sheet_name=sheet_name, header=None, skiprows=header_row+1)
                    
                    return self.process_data_by_index(df_data, product_col_idx, price_col_idx, supplier_name, sheet_name, header_row)
                else:
                    product_col = df_processed.columns[product_col_idx]
                    price_col = df_processed.columns[price_col_idx]
            else:
                df_with_header = pd.read_excel(excel_file, sheet_name=sheet_name)
                df_processed = df_with_header
                product_col = df_processed.columns[product_col_idx]
                price_col = df_processed.columns[price_col_idx]
            
            self.log_debug(f"🎯 COLUMNAS FINALES - Producto: '{product_col}' (col {product_col_idx}), Precio: '{price_col}' (col {price_col_idx})")
            
            # Procesar filas
            return self.process_data_by_column_name(df_processed, product_col, price_col, supplier_name, sheet_name, header_row)
            
        except Exception as e:
            error = f"Error procesando hoja {sheet_name}: {str(e)}"
            self.log_debug(f"💥 {error}", "ERROR")
            return {'sheet': sheet_name, 'error': error, 'products': []}

    def process_data_by_index(self, df_data, product_col_idx, price_col_idx, supplier_name, sheet_name, header_row):
        """Procesa datos usando índices numéricos"""
        products = []
        stats = {'processed': 0, 'valid_products': 0, 'invalid_prices': 0, 'empty_products': 0}
        
        for idx, row in df_data.iterrows():
            stats['processed'] += 1
            
            try:
                if product_col_idx < len(row) and price_col_idx < len(row):
                    product = row.iloc[product_col_idx]
                    price_raw = row.iloc[price_col_idx]
                    
                    if pd.isna(product) or not str(product).strip():
                        stats['empty_products'] += 1
                        continue
                    
                    price = self.clean_price(price_raw)
                    
                    if price is not None and price > 0:
                        products.append({
                            'product': str(product).strip(),
                            'price': price,
                            'supplier': supplier_name,
                            'sheet': sheet_name,
                            'location': DEFAULT_LOCATIONS.get(supplier_name, 'Buenos Aires, Argentina'),
                            'id': f"{supplier_name}_{sheet_name}_{idx}_{len(products)}"
                        })
                        stats['valid_products'] += 1
                    else:
                        stats['invalid_prices'] += 1
                
            except Exception as row_error:
                self.log_debug(f"❌ Error fila {idx}: {row_error}", "WARNING")
                continue
        
        return {
            'sheet': sheet_name,
            'products': products,
            'stats': stats,
            'columns_used': {'product': f'Col_{product_col_idx}', 'price': f'Col_{price_col_idx}'},
            'header_row': header_row
        }

    def process_data_by_column_name(self, df_processed, product_col, price_col, supplier_name, sheet_name, header_row):
        """Procesa datos usando nombres de columnas"""
        products = []
        stats = {'processed': 0, 'valid_products': 0, 'invalid_prices': 0, 'empty_products': 0}
        
        for idx, row in df_processed.iterrows():
            stats['processed'] += 1
            
            try:
                product = row[product_col]
                price_raw = row[price_col]
                
                if pd.isna(product) or not str(product).strip():
                    stats['empty_products'] += 1
                    continue
                
                price = self.clean_price(price_raw)
                
                if price is not None and price > 0:
                    products.append({
                        'product': str(product).strip(),
                        'price': price,
                        'supplier': supplier_name,
                        'sheet': sheet_name,
                        'location': DEFAULT_LOCATIONS.get(supplier_name, 'Buenos Aires, Argentina'),
                        'id': f"{supplier_name}_{sheet_name}_{idx}_{len(products)}"
                    })
                    stats['valid_products'] += 1
                else:
                    stats['invalid_prices'] += 1
                    
            except Exception as row_error:
                self.log_debug(f"❌ Error fila {idx}: {row_error}", "WARNING")
                continue
        
        self.log_debug(f"📈 ESTADÍSTICAS: {stats}")
        
        return {
            'sheet': sheet_name,
            'products': products,
            'stats': stats,
            'columns_used': {'product': product_col, 'price': price_col},
            'header_row': header_row
        }
    
    def analyze_content_structure(self, df):
        """Analiza la estructura del contenido para sugerir alternativas"""
        try:
            analysis = {
                'total_rows': len(df),
                'total_cols': len(df.columns) if not df.empty else 0,
                'non_empty_cells_per_col': [],
                'sample_data': []
            }
            
            if df.empty:
                return analysis
            
            # Analizar cada columna
            for col_idx in range(min(df.shape[1], 10)):  # Máximo 10 columnas
                non_empty = df.iloc[:, col_idx].count()
                analysis['non_empty_cells_per_col'].append({
                    'column': col_idx,
                    'non_empty_cells': non_empty,
                    'percentage': round((non_empty / len(df)) * 100, 1)
                })
            
            # Muestra de las primeras 3 filas
            for row_idx in range(min(3, len(df))):
                row_data = [str(val)[:20] if pd.notna(val) else '' for val in df.iloc[row_idx].values[:5]]
                analysis['sample_data'].append(row_data)
            
            return analysis
        except Exception as e:
            return {'error': str(e)}
    
    def deep_column_analysis(self, df, col_idx):
        """Análisis profundo de una columna específica"""
        try:
            if df.empty or col_idx >= df.shape[1]:
                return "Columna fuera de rango"
            
            col_data = df.iloc[:, col_idx].dropna()
            if col_data.empty:
                return "Columna vacía"
            
            sample_values = col_data.head(5).tolist()
            analysis = {
                'sample_values': [str(val)[:30] for val in sample_values],
                'total_non_empty': len(col_data),
                'unique_values': len(col_data.unique())
            }
            
            return analysis
        except Exception as e:
            return f"Error en análisis: {str(e)}"
# Crear instancia del procesador
processor = PriceListProcessor()

@app.route('/')
def index():
    return render_template('index.html')

def analyze_processing_failure(debug_summary, supplier_name):
    """Analiza por qué falló el procesamiento y genera diagnóstico detallado"""
    
    failure_analysis = {
        'supplier': supplier_name,
        'primary_issues': [],
        'sheet_analysis': [],
        'column_detection_issues': [],
        'data_quality_issues': [],
        'recommendations': []
    }
    
    # Analizar detalles de hojas si están disponibles
    sheet_details = debug_summary.get('sheet_details', [])
    
    for sheet_info in sheet_details:
        sheet_name = sheet_info.get('sheet', 'Unknown')
        
        sheet_analysis = {
            'sheet_name': sheet_name,
            'status': 'failed' if sheet_info.get('error') else 'processed',
            'error': sheet_info.get('error'),
            'products_found': len(sheet_info.get('products', [])),
            'stats': sheet_info.get('stats', {})
        }
        
        # Identificar problemas específicos
        if sheet_info.get('error'):
            error_msg = sheet_info['error'].lower()
            
            if 'no se encontró columna de producto' in error_msg:
                failure_analysis['column_detection_issues'].append({
                    'sheet': sheet_name,
                    'issue': 'product_column_not_found',
                    'detail': sheet_info['error']
                })
            
            if 'no se encontró columna de precio' in error_msg:
                failure_analysis['column_detection_issues'].append({
                    'sheet': sheet_name,
                    'issue': 'price_column_not_found',
                    'detail': sheet_info['error']
                })
                
            if 'misma columna' in error_msg:
                failure_analysis['column_detection_issues'].append({
                    'sheet': sheet_name,
                    'issue': 'same_column_detected',
                    'detail': sheet_info['error']
                })
        
        # Analizar calidad de datos
        stats = sheet_info.get('stats', {})
        if stats:
            if stats.get('invalid_prices', 0) > stats.get('valid_products', 0):
                failure_analysis['data_quality_issues'].append({
                    'sheet': sheet_name,
                    'issue': 'too_many_invalid_prices',
                    'invalid_prices': stats.get('invalid_prices', 0),
                    'valid_products': stats.get('valid_products', 0)
                })
            
            if stats.get('empty_products', 0) > stats.get('processed', 1) * 0.5:
                failure_analysis['data_quality_issues'].append({
                    'sheet': sheet_name,
                    'issue': 'too_many_empty_products',
                    'empty_products': stats.get('empty_products', 0),
                    'processed': stats.get('processed', 0)
                })
        
        failure_analysis['sheet_analysis'].append(sheet_analysis)
    
    # Determinar problemas primarios
    if failure_analysis['column_detection_issues']:
        failure_analysis['primary_issues'].append('column_detection_failed')
    
    if failure_analysis['data_quality_issues']:
        failure_analysis['primary_issues'].append('poor_data_quality')
    
    if not sheet_details:
        failure_analysis['primary_issues'].append('no_sheets_processed')
    
    return failure_analysis

def generate_recommendations(failure_analysis):
    """Genera recomendaciones específicas basadas en el análisis de fallo"""
    
    recommendations = []
    
    # Recomendaciones para detección de columnas
    for issue in failure_analysis.get('column_detection_issues', []):
        if issue['issue'] == 'product_column_not_found':
            recommendations.append({
                'type': 'column_naming',
                'priority': 'high',
                'message': f"En la hoja '{issue['sheet']}': Asegúrese de que haya una columna con nombre como 'Producto', 'Descripción', 'Nombre', etc.",
                'detailed_suggestion': "Las columnas de producto deben tener headers claros. Evite espacios extra o caracteres especiales."
            })
        
        elif issue['issue'] == 'price_column_not_found':
            recommendations.append({
                'type': 'column_naming',
                'priority': 'high', 
                'message': f"En la hoja '{issue['sheet']}': Asegúrese de que haya una columna con nombre como 'Precio', 'Cost', 'Valor', etc.",
                'detailed_suggestion': "Las columnas de precio deben contener valores numéricos. Evite texto como 'Consultar' o 'N/A'."
            })
    
    # Recomendaciones para calidad de datos
    for issue in failure_analysis.get('data_quality_issues', []):
        if issue['issue'] == 'too_many_invalid_prices':
            recommendations.append({
                'type': 'data_quality',
                'priority': 'medium',
                'message': f"En la hoja '{issue['sheet']}': Muchos precios no son válidos ({issue['invalid_prices']} inválidos vs {issue['valid_products']} válidos)",
                'detailed_suggestion': "Revise que los precios sean números sin texto adicional. Evite valores como 'Consultar', ', o celdas vacías."
            })
        
        elif issue['issue'] == 'too_many_empty_products':
            recommendations.append({
                'type': 'data_quality',
                'priority': 'low',
                'message': f"En la hoja '{issue['sheet']}': Muchas filas sin nombre de producto ({issue['empty_products']} vacías de {issue['processed']} procesadas)",
                'detailed_suggestion': "Elimine filas vacías o asegúrese de que cada producto tenga un nombre válido."
            })
    
    # Recomendaciones generales
    if not recommendations:
        recommendations.append({
            'type': 'general',
            'priority': 'high',
            'message': "No se detectaron problemas específicos. El archivo puede tener un formato no estándar.",
            'detailed_suggestion': "Intente usar un archivo Excel simple con columnas claras: 'Producto' y 'Precio' en la primera fila."
        })
    
    return recommendations

def analyze_success(debug_summary, total_products):
    """Analiza el éxito del procesamiento y genera resumen"""
    
    success_summary = {
        'total_products_loaded': total_products,
        'sheets_summary': [],
        'processing_efficiency': {},
        'data_quality_score': 0
    }
    
    sheet_details = debug_summary.get('sheet_details', [])
    total_processed = 0
    total_valid = 0
    total_invalid_prices = 0
    
    for sheet_info in sheet_details:
        stats = sheet_info.get('stats', {})
        sheet_products = len(sheet_info.get('products', []))
        
        sheet_summary = {
            'sheet_name': sheet_info.get('sheet', 'Unknown'),
            'products_extracted': sheet_products,
            'rows_processed': stats.get('processed', 0),
            'success_rate': (sheet_products / max(stats.get('processed', 1), 1)) * 100,
            'columns_used': sheet_info.get('columns_used', {}),
            'header_row': sheet_info.get('header_row', 0)
        }
        
        success_summary['sheets_summary'].append(sheet_summary)
        
        # Acumular estadísticas
        total_processed += stats.get('processed', 0)
        total_valid += sheet_products
        total_invalid_prices += stats.get('invalid_prices', 0)
    
    # Calcular eficiencia y calidad
    if total_processed > 0:
        success_summary['processing_efficiency'] = {
            'total_rows_processed': total_processed,
            'products_extracted': total_valid,
            'extraction_rate': (total_valid / total_processed) * 100,
            'invalid_prices_found': total_invalid_prices,
            'price_validation_rate': (total_valid / max(total_valid + total_invalid_prices, 1)) * 100
        }
        
        # Score de calidad (0-100)
        extraction_rate = (total_valid / total_processed) * 100
        price_validation_rate = (total_valid / max(total_valid + total_invalid_prices, 1)) * 100
        
        success_summary['data_quality_score'] = round((extraction_rate + price_validation_rate) / 2, 1)
    
    return success_summary
@app.route('/upload', methods=['POST'])
def upload_file():
    # 1) Validaciones básicas
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'No se seleccionó archivo'}), 400

    file = request.files['file']
    supplier_name = (request.form.get('supplier_name') or '').strip() or 'Proveedor Sin Nombre'

    # NUEVO: leer dirección y teléfono
    supplier_address = (request.form.get('supplier_address') or '').strip()
    supplier_phone   = (request.form.get('supplier_phone') or '').strip()
    supplier_email   = (request.form.get('supplier_email') or '').strip()

    if file.filename == '':
        return jsonify({'success': False, 'message': 'No se seleccionó archivo'}), 400

    if not file.filename.lower().endswith(('.xlsx', '.xls')):
        return jsonify({'success': False, 'message': 'Tipo de archivo no soportado. Use .xlsx o .xls'}), 400

    # 2) Guardar temporalmente
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)

    proveedor_status = 'SKIPPED'
    db_status = 'SKIPPED'
    debug_summary = {}

    try:
        file.save(filepath)
        print(f"📁 Archivo guardado temporalmente: {filepath}")

        # 3) Guardar/actualizar datos del proveedor
        try:
            upsert_proveedor(supplier_name, supplier_address, supplier_phone, supplier_email)
            proveedor_status = 'OK'
            print(f"✅ Proveedor actualizado: {supplier_name}")
        except Exception as e:
            proveedor_status = f'ERROR proveedor: {e}'
            print(f"❌ Error actualizando proveedor: {e}")

        # 4) Procesar Excel → obtener productos con debugging mejorado
        print(f"🔄 Iniciando procesamiento de {filepath} para {supplier_name}")
        products, debug_info = processor.process_excel_file(filepath, supplier_name)
        
        # Almacenar información de debug completa
        debug_summary = debug_info if isinstance(debug_info, dict) else {'debug_logs': debug_info}
        
        print(f"📊 Procesamiento completado: {len(products)} productos encontrados")

        # 5) Análisis detallado de por qué falló (si es el caso)
        if not products:
            failure_analysis = analyze_processing_failure(debug_summary, supplier_name)
            
            return jsonify({
                'success': False,
                'message': 'No se pudieron extraer productos del archivo',
                'supplier': supplier_name,
                'filename': file.filename,
                'proveedor_status': proveedor_status,
                'db_status': db_status,
                'debug_info': debug_summary,
                'failure_analysis': failure_analysis,
                'recommendations': generate_recommendations(failure_analysis)
            }), 200

        # 6) Guardar productos en MySQL
        try:
            print(f"💾 Guardando {len(products)} productos en BD...")
            guardar_productos_en_bd(supplier_name, products)
            db_status = 'OK'
            print(f"✅ Productos guardados exitosamente en BD")
        except Exception as db_err:
            db_status = f'ERROR DB: {db_err}'
            print(f"❌ Error guardando en BD: {db_err}")

        # 7) Respuesta exitosa con información detallada
        success_summary = analyze_success(debug_summary, len(products))
        
        return jsonify({
            'success': True,
            'message': f'Archivo procesado exitosamente. {len(products)} productos cargados.',
            'supplier': supplier_name,
            'filename': file.filename,
            'total_products': len(products),
            'proveedor_status': proveedor_status,
            'db_status': db_status,
            'processing_summary': success_summary,
            'debug_available': True,
            'debug_info': {
                'sheets_processed': debug_summary.get('sheets_processed', 0),
                'sheets_successful': debug_summary.get('sheets_successful', 0),
                'structure_analysis': debug_summary.get('structure_analysis', {}),
                'recent_logs': debug_summary.get('debug_logs', {}).get('recent_logs', [])
            }
        }), 200

    except Exception as e:
        # Error general en procesamiento/carga
        error_details = {
            'error_type': type(e).__name__,
            'error_message': str(e),
            'supplier': supplier_name,
            'filename': file.filename,
            'debug_summary': debug_summary
        }
        
        print(f"💥 ERROR CRÍTICO: {error_details}")
        
        return jsonify({
            'success': False,
            'message': f'Error crítico procesando archivo: {str(e)}',
            'error_details': error_details,
            'proveedor_status': proveedor_status,
            'db_status': db_status
        }), 500

    finally:
        # 8) Limpiar archivo temporal siempre
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                print(f"🗑️ Archivo temporal eliminado: {filepath}")
        except Exception as cleanup_error:
            print(f"⚠️ No se pudo eliminar archivo temporal: {cleanup_error}")
            
@app.route('/search')
@login_required
def search_products():
    q = request.args.get('q','').strip()
    if not q:
        return jsonify({'results': [], 'total': 0})
    
    filas = buscar_productos_bd(q, 300)
    results = [{
        'id': f"{r['proveedor']}|{r['producto']}|{float(r['precio']):.4f}",
        'product': r['producto'],
        'price': float(r['precio']),
        'price_formatted': f"${float(r['precio']):,.2f}",  # Cambiado a $ en lugar de €
        'supplier': r['proveedor'],
        'location': DEFAULT_LOCATIONS.get(r['proveedor'], 'Argentina'),
        'updated_at': (r['actualizado_a'].isoformat() if r.get('actualizado_a') else None)
    } for r in filas]
    
    return jsonify({
        'results': results, 
        'total': len(results), 
        'query': q,
        'suppliers_count': len(set(r['proveedor'] for r in filas))
    })
# Agregar estos endpoints al final de tu app.py

@app.route('/debug/analyze_file', methods=['POST'])
@login_required
def debug_analyze_file():
    """Endpoint para analizar un archivo sin procesarlo completamente"""
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'No file provided'}), 400
    
    file = request.files['file']
    if not file.filename.lower().endswith(('.xlsx', '.xls')):
        return jsonify({'success': False, 'message': 'Invalid file type'}), 400
    
    # Guardar temporalmente
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    try:
        file.save(filepath)
        
        # Solo analizar estructura, no procesar
        structure = processor.analyze_excel_structure(filepath)
        
        return jsonify({
            'success': True,
            'filename': file.filename,
            'analysis': structure,
            'message': 'Análisis completado'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'message': 'Error analizando archivo'
        }), 500
        
    finally:
        # Limpiar archivo temporal
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except:
            pass

@app.route('/debug/test_price_cleaning', methods=['POST'])
@login_required
def debug_test_price_cleaning():
    """Endpoint para probar la limpieza de precios con valores específicos"""
    data = request.get_json()
    test_values = data.get('test_values', [])
    
    if not test_values:
        # Valores de prueba por defecto
        test_values = [
            "$123.45", "€50,30", "1234", "12.345,67", "N/A", 
            "Consultar", "123X2", "0.50", "999999", "",
            "12,50", "1.234.567,89", "$", "abc123", "123.456"
        ]
    
    results = []
    processor_temp = PriceListProcessor()  # Nueva instancia para test limpio
    
    for test_value in test_values:
        try:
            cleaned = processor_temp.clean_price(test_value)
            results.append({
                'input': test_value,
                'output': cleaned,
                'status': 'accepted' if cleaned is not None else 'rejected',
                'type': type(test_value).__name__
            })
        except Exception as e:
            results.append({
                'input': test_value,
                'output': None,
                'status': 'error',
                'error': str(e)
            })
    
    return jsonify({
        'success': True,
        'test_results': results,
        'summary': {
            'total_tested': len(test_values),
            'accepted': len([r for r in results if r['status'] == 'accepted']),
            'rejected': len([r for r in results if r['status'] == 'rejected']),
            'errors': len([r for r in results if r['status'] == 'error'])
        }
    })

@app.route('/debug/column_detection', methods=['POST'])
@login_required
def debug_column_detection():
    """Endpoint para probar detección de columnas con headers específicos"""
    data = request.get_json()
    test_headers = data.get('headers', [])
    
    if not test_headers:
        # Headers de prueba por defecto
        test_headers = [
            ['Producto', 'Precio', 'Stock'],
            ['DESCRIPCION', 'COSTO', 'CANTIDAD'],
            ['Item', 'Valor', 'Disponible'],
            ['Nombre del Producto', 'Precio Unitario', 'Existencias'],
            ['Col1', 'Col2', 'Col3'],  # Sin nombres descriptivos
            ['', 'precio', ''],        # Header parcialmente vacío
            ['Artículo', 'Importe c/IVA', 'Observaciones']
        ]
    
    processor_temp = PriceListProcessor()
    results = []
    
    for i, headers in enumerate(test_headers):
        # Crear DataFrame temporal para prueba
        import pandas as pd
        df_test = pd.DataFrame(columns=headers)
        
        # Probar detección
        product_idx = processor_temp.find_column_index(df_test, processor_temp.possible_product_columns)
        price_idx = processor_temp.find_column_index(df_test, processor_temp.possible_price_columns)
        
        results.append({
            'test_case': i + 1,
            'headers': headers,
            'product_column_detected': {
                'index': product_idx,
                'column_name': headers[product_idx] if product_idx is not None else None
            },
            'price_column_detected': {
                'index': price_idx,
                'column_name': headers[price_idx] if price_idx is not None else None
            },
            'detection_success': product_idx is not None and price_idx is not None,
            'same_column_error': product_idx == price_idx if both_detected(product_idx, price_idx) else False
        })
    
    return jsonify({
        'success': True,
        'detection_results': results,
        'summary': {
            'total_tested': len(test_headers),
            'successful_detections': len([r for r in results if r['detection_success']]),
            'failed_detections': len([r for r in results if not r['detection_success']]),
            'same_column_errors': len([r for r in results if r['same_column_error']])
        },
        'column_patterns': {
            'product_patterns': processor_temp.possible_product_columns,
            'price_patterns': processor_temp.possible_price_columns
        }
    })

def both_detected(prod_idx, price_idx):
    """Helper para verificar si ambos índices fueron detectados"""
    return prod_idx is not None and price_idx is not None

@app.route('/debug/processing_stats')
@login_required
def debug_processing_stats():
    """Estadísticas generales de procesamiento desde la BD"""
    try:
        conn = get_connection()
        cur = conn.cursor(dictionary=True)
        
        # Estadísticas por proveedor
        cur.execute("""
            SELECT 
                p.proveedor,
                COUNT(*) as total_productos,
                AVG(p.precio) as precio_promedio,
                MIN(p.precio) as precio_minimo,
                MAX(p.precio) as precio_maximo,
                MAX(p.actualizado_a) as ultima_actualizacion,
                pr.direccion,
                pr.telefono,
                pr.email
            FROM productos p
            LEFT JOIN proveedores_config pr ON pr.proveedor = p.proveedor
            GROUP BY p.proveedor, pr.direccion, pr.telefono, pr.email
            ORDER BY total_productos DESC
        """)
        
        supplier_stats = cur.fetchall()
        
        # Estadísticas globales
        cur.execute("SELECT COUNT(DISTINCT proveedor) as total_suppliers FROM productos")
        total_suppliers = cur.fetchone()['total_suppliers']
        
        cur.execute("SELECT COUNT(*) as total_products FROM productos")
        total_products = cur.fetchone()['total_products']
        
        cur.execute("SELECT AVG(precio) as global_avg_price FROM productos")
        global_avg_price = cur.fetchone()['global_avg_price']
        
        # Productos con precios potencialmente problemáticos
        cur.execute("""
            SELECT proveedor, producto, precio 
            FROM productos 
            WHERE precio < 1 OR precio > 100000 
            ORDER BY precio DESC 
            LIMIT 20
        """)
        problematic_prices = cur.fetchall()
        
        conn.close()
        
        return jsonify({
            'success': True,
            'global_stats': {
                'total_suppliers': total_suppliers,
                'total_products': total_products,
                'global_avg_price': float(global_avg_price) if global_avg_price else 0
            },
            'supplier_details': [
                {
                    'supplier': s['proveedor'],
                    'products': s['total_productos'],
                    'avg_price': float(s['precio_promedio']) if s['precio_promedio'] else 0,
                    'price_range': {
                        'min': float(s['precio_minimo']) if s['precio_minimo'] else 0,
                        'max': float(s['precio_maximo']) if s['precio_maximo'] else 0
                    },
                    'last_update': s['ultima_actualizacion'].isoformat() if s['ultima_actualizacion'] else None,
                    'contact_info': {
                        'address': s['direccion'] or '',
                        'phone': s['telefono'] or '',
                        'email': s['email'] or ''
                    }
                } for s in supplier_stats
            ],
            'data_quality_alerts': [
                {
                    'supplier': p['proveedor'],
                    'product': p['producto'],
                    'price': float(p['precio']),
                    'alert_type': 'very_low_price' if p['precio'] < 1 else 'very_high_price'
                } for p in problematic_prices
            ]
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'message': 'Error obteniendo estadísticas de debugging'
        }), 500

@app.route('/debug/logs/<supplier_name>')
@login_required
def debug_get_logs(supplier_name):
    """Obtiene logs detallados del último procesamiento de un proveedor"""
    # Esto requeriría almacenar logs en BD o archivos
    # Por ahora retorna información básica
    
    try:
        conn = get_connection()
        cur = conn.cursor(dictionary=True)
        
        # Información del proveedor
        cur.execute("""
            SELECT 
                COUNT(*) as total_productos,
                AVG(precio) as precio_promedio,
                MIN(actualizado_a) as primera_carga,
                MAX(actualizado_a) as ultima_carga
            FROM productos 
            WHERE proveedor = %s
        """, (supplier_name,))
        
        supplier_info = cur.fetchone()
        
        # Muestra de productos
        cur.execute("""
            SELECT producto, precio, actualizado_a
            FROM productos 
            WHERE proveedor = %s 
            ORDER BY actualizado_a DESC 
            LIMIT 10
        """, (supplier_name,))
        
        sample_products = cur.fetchall()
        
        conn.close()
        
        return jsonify({
            'success': True,
            'supplier': supplier_name,
            'summary': {
                'total_products': supplier_info['total_productos'],
                'avg_price': float(supplier_info['precio_promedio']) if supplier_info['precio_promedio'] else 0,
                'first_load': supplier_info['primera_carga'].isoformat() if supplier_info['primera_carga'] else None,
                'last_load': supplier_info['ultima_carga'].isoformat() if supplier_info['ultima_carga'] else None
            },
            'sample_products': [
                {
                    'product': p['producto'],
                    'price': float(p['precio']),
                    'loaded_at': p['actualizado_a'].isoformat() if p['actualizado_a'] else None
                } for p in sample_products
            ],
            'note': 'Para logs detallados de procesamiento, debe implementarse sistema de logging persistente'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
@app.route('/lists')
@login_required
def get_loaded_lists():
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT p.proveedor,
                   COUNT(*)           AS total_products,
                   MAX(p.actualizado_a) AS last_update,
                   pr.direccion,
                   pr.telefono,
                   pr.email
            FROM productos p
            LEFT JOIN proveedores_config pr ON pr.proveedor = p.proveedor
            GROUP BY p.proveedor, pr.direccion, pr.telefono, pr.email
            ORDER BY p.proveedor
        """)
        rows = cur.fetchall()
        lists_info = [{
            'supplier': r['proveedor'],
            'filename': r['proveedor'] + '.xlsx',
            'upload_date': (r['last_update'].isoformat() if r['last_update'] else None),
            'total_products': int(r['total_products']),
            'direccion': r.get('direccion') or '',
            'telefono': r.get('telefono') or '',
            'email': r.get('email') or ''
        } for r in rows]
        return jsonify({'lists': lists_info, 'total': len(lists_info)})
    finally:
        conn.close()


@app.route('/stats')
@login_required
def stats():
    """Estadisticas directas desde la BD."""
    conn = get_connection()
    try:
        cur = conn.cursor()

        # Total de listas = cantidad de proveedores unicos
        cur.execute("SELECT COUNT(DISTINCT proveedor) FROM productos")
        total_lists = cur.fetchone()[0] or 0

        # Total de productos
        cur.execute("SELECT COUNT(*) FROM productos")
        total_products = cur.fetchone()[0] or 0

        # ultima actualizacion global (por si queres mostrarla mas adelante)
        cur.execute("SELECT MAX(actualizado_a) FROM productos")
        last_update_row = cur.fetchone()
        last_update = (
            last_update_row[0].isoformat() if last_update_row and last_update_row[0] else None
        )

        return jsonify({
            'success': True,
            'total_lists': int(total_lists),
            'total_products': int(total_products),
            'last_update': last_update
        })
    finally:
        conn.close()


## CART ##
# Usar threading.Lock para evitar problemas de concurrencia
import threading
from collections import defaultdict

# Cambiar a defaultdict con lock para thread safety
cart_lock = threading.Lock()
user_carts = defaultdict(list)

def get_user_cart(user):
    """Obtener carrito del usuario de forma thread-safe"""
    with cart_lock:
        return user_carts[user].copy()

def set_user_cart(user, cart):
    """Establecer carrito del usuario de forma thread-safe"""
    with cart_lock:
        user_carts[user] = cart

@app.route('/cart/add', methods=['POST'])
@login_required
def add_to_cart():
    """Agregar producto al carrito del usuario."""
    try:
        user = session.get('user')
        print(f"🔵 [DEBUG ADD] Usuario: '{user}' (tipo: {type(user)})")
        print(f"🔵 [DEBUG ADD] Session completa: {dict(session)}")
        if not user:
            return jsonify({'success': False, 'error': 'Usuario no autenticado'}), 401
            
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Datos inválidos'}), 400
        
        # Validar datos requeridos
        required_fields = ['product_id', 'product_name', 'price', 'supplier']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'success': False, 'error': f'Campo {field} requerido'}), 400
        
        product_id = str(data.get('product_id')).strip()
        product_name = str(data.get('product_name')).strip()
        supplier = str(data.get('supplier')).strip()
        
        try:
            price = float(data.get('price', 0))
            quantity = int(data.get('quantity', 1))
        except (ValueError, TypeError):
            return jsonify({'success': False, 'error': 'Precio o cantidad inválidos'}), 400
        
        if price <= 0 or quantity <= 0:
            return jsonify({'success': False, 'error': 'Precio y cantidad deben ser positivos'}), 400
        
        print(f"🔵 [DEBUG] Usuario: {user} - Agregando: {product_name}, ${price}, {supplier}")
        
        # Obtener carrito actual de forma thread-safe
        cart = get_user_cart(user)
        print(f"🔵 [DEBUG ADD] Carrito actual antes de modificar: {cart}")
        
        # Buscar producto existente (usar product_id Y supplier como clave única)
        product_found = False
        for item in cart:
            if item['id'] == product_id and item['supplier'] == supplier:
                item['quantity'] += quantity
                product_found = True
                print(f"🔵 [DEBUG] Producto existente actualizado. Nueva cantidad: {item['quantity']}")
                break
        
        # Si no existe, agregarlo
        if not product_found:
            new_item = {
                'id': product_id,
                'product': product_name,
                'price': price,
                'supplier': supplier,
                'quantity': quantity
            }
            cart.append(new_item)
            print(f"🔵 [DEBUG] Nuevo producto agregado al carrito. Carrito ahora: {cart}")
        
        # ✅ LOGS CRÍTICOS AGREGADOS:
        print(f"🔵 [DEBUG ADD] ANTES de guardar - user_carts keys: {list(user_carts.keys())}")
        print(f"🔵 [DEBUG ADD] ANTES de guardar - carrito a guardar: {cart}")
        
        # Guardar carrito actualizado
        set_user_cart(user, cart)
        
        # ✅ VERIFICACIÓN INMEDIATA después de guardar:
        print(f"🔵 [DEBUG ADD] DESPUÉS de guardar - user_carts: {dict(user_carts)}")
        verification_cart = get_user_cart(user)
        print(f"🔵 [DEBUG ADD] VERIFICACIÓN inmediata - carrito recuperado: {verification_cart}")
        
        return jsonify({
            'success': True,
            'cart_count': len(cart),
            'message': f'{product_name} agregado al carrito',
            'total_items': sum(item['quantity'] for item in cart)
        })
        
    except Exception as e:
        print(f"❌ [ERROR] Error en add_to_cart: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': 'Error interno del servidor'}), 500

@app.route('/cart/get')
@login_required
def get_cart():
    """Obtener carrito del usuario"""
    try:
        user = session.get('user')
        print(f"🟡 [DEBUG GET] Usuario: '{user}' (tipo: {type(user)})")
        print(f"🟡 [DEBUG GET] user_carts keys disponibles: {list(user_carts.keys())}")
        print(f"🟡 [DEBUG GET] user_carts completo: {dict(user_carts)}")
        
        if not user:
            return jsonify({'success': False, 'error': 'Usuario no autenticado'}), 401
        
        cart = get_user_cart(user)
        print(f"🟡 [DEBUG GET] Carrito obtenido para '{user}': {cart}")
        
        total = sum(item['price'] * item['quantity'] for item in cart)
        
        # Agrupar por proveedor
        suppliers = defaultdict(list)
        for item in cart:
            suppliers[item['supplier']].append(item)
        
        result = {
            'success': True,
            'cart': cart,
            'total': total,
            'total_formatted': f"${total:,.2f}",
            'suppliers': dict(suppliers),
            'items_count': len(cart),
            'total_quantity': sum(item['quantity'] for item in cart)
        }
        
        print(f"🟡 [DEBUG GET] Respuesta a enviar: {result}")
        return jsonify(result)
        
    except Exception as e:
        print(f"❌ [ERROR] Error en get_cart: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': 'Error interno del servidor'}), 500

@app.route('/cart/update', methods=['POST'])
@login_required
def update_cart():
    """Actualizar cantidad de producto en carrito"""
    try:
        user = session.get('user')
        if not user:
            return jsonify({'success': False, 'error': 'Usuario no autenticado'}), 401
            
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Datos inválidos'}), 400
        
        product_id = str(data.get('product_id', '')).strip()
        supplier = str(data.get('supplier', '')).strip()  # Agregar supplier para identificación única
        
        try:
            quantity = int(data.get('quantity', 1))
        except (ValueError, TypeError):
            return jsonify({'success': False, 'error': 'Cantidad inválida'}), 400
        
        if not product_id:
            return jsonify({'success': False, 'error': 'ID de producto requerido'}), 400
        
        cart = get_user_cart(user)
        
        # Buscar producto por ID y supplier
        for i, item in enumerate(cart):
            if item['id'] == product_id and (not supplier or item['supplier'] == supplier):
                if quantity <= 0:
                    cart.pop(i)
                    print(f"[DEBUG] Producto {product_id} eliminado del carrito")
                else:
                    item['quantity'] = quantity
                    print(f"[DEBUG] Producto {product_id} actualizado a cantidad: {quantity}")
                
                set_user_cart(user, cart)
                return jsonify({
                    'success': True,
                    'cart_count': len(cart),
                    'total_quantity': sum(item['quantity'] for item in cart)
                })
        
        return jsonify({'success': False, 'error': 'Producto no encontrado en el carrito'}), 404
        
    except Exception as e:
        print(f"[ERROR] Error en update_cart: {e}")
        return jsonify({'success': False, 'error': 'Error interno del servidor'}), 500

@app.route('/cart/remove', methods=['POST'])
@login_required
def remove_from_cart():
    """Eliminar producto del carrito"""
    try:
        user = session.get('user')
        if not user:
            return jsonify({'success': False, 'error': 'Usuario no autenticado'}), 401
            
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Datos inválidos'}), 400
        
        product_id = str(data.get('product_id', '')).strip()
        supplier = str(data.get('supplier', '')).strip()
        
        if not product_id:
            return jsonify({'success': False, 'error': 'ID de producto requerido'}), 400
        
        cart = get_user_cart(user)
        
        # Buscar y eliminar producto
        for i, item in enumerate(cart):
            if item['id'] == product_id and (not supplier or item['supplier'] == supplier):
                removed_item = cart.pop(i)
                set_user_cart(user, cart)
                print(f"[DEBUG] Producto {removed_item['product']} eliminado del carrito")
                return jsonify({
                    'success': True,
                    'message': f"{removed_item['product']} eliminado del carrito",
                    'cart_count': len(cart)
                })
        
        return jsonify({'success': False, 'error': 'Producto no encontrado en el carrito'}), 404
        
    except Exception as e:
        print(f"[ERROR] Error en remove_from_cart: {e}")
        return jsonify({'success': False, 'error': 'Error interno del servidor'}), 500

@app.route('/cart/clear')
@login_required
def clear_cart():
    """Limpiar carrito completo"""
    try:
        user = session.get('user')
        if not user:
            return jsonify({'success': False, 'error': 'Usuario no autenticado'}), 401
        
        set_user_cart(user, [])
        print(f"[DEBUG] Carrito limpiado para usuario: {user}")
        
        return jsonify({
            'success': True,
            'message': 'Carrito limpiado',
            'cart_count': 0
        })
        
    except Exception as e:
        print(f"[ERROR] Error en clear_cart: {e}")
        return jsonify({'success': False, 'error': 'Error interno del servidor'}), 500

@app.route('/cart/generate_pdfs', methods=['POST'])
@login_required
def generate_pdfs():
    """Generar PDFs por proveedor"""
    try:
        user = session.get('user')
        if not user:
            return jsonify({'success': False, 'error': 'Usuario no autenticado'}), 401
        
        # Obtener datos del negocio desde la BD
        business_data_from_db = get_user_business_data(user)
        
        # Verificar datos del negocio
        if not business_data_from_db or not all([
            business_data_from_db.get('business_name', '').strip(),
            business_data_from_db.get('address', '').strip(),
            business_data_from_db.get('phone', '').strip()
        ]):
            return jsonify({
                'success': False,
                'error': 'Datos del negocio requeridos',
                'show_business_form': True,
                'message': 'Primero complete la información de su comercio'
            }), 400
        
        cart = get_user_cart(user)
        if not cart:
            return jsonify({'success': False, 'error': 'Carrito vacío'}), 400
        
        # Agrupar productos por proveedor
        suppliers = defaultdict(list)
        for item in cart:
            suppliers[item['supplier']].append(item)
        
        # Generar PDFs
        generated_pdfs = []
        whatsapp_links = []
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_user = user.replace(' ', '_').replace('/', '_')
        
        for supplier, items in suppliers.items():
            safe_supplier = supplier.replace(' ', '_').replace('/', '_')
            filename = f"pedido_{safe_supplier}_{safe_user}_{timestamp}.pdf"
            
            try:
                pdf_path = generate_pdf_for_supplier(supplier, items, business_data_from_db, filename)
                total_supplier = sum(item['quantity'] * item['price'] for item in items)
                
                generated_pdfs.append({
                    'supplier': supplier,
                    'filename': filename,
                    'path': pdf_path,
                    'items_count': len(items),
                    'total': total_supplier,
                    'total_formatted': f"${total_supplier:,.2f}"
                })
                
                # Crear mensaje de WhatsApp
                whatsapp_message = create_whatsapp_message(supplier, items, business_data_from_db, total_supplier)
                phone = get_supplier_phone(supplier)
                phone_clean = _normalize_phone(phone)
                
                if phone_clean:
                    wa_url = f"https://wa.me/{phone_clean}?text={urllib.parse.quote(whatsapp_message)}"
                else:
                    wa_url = f"https://wa.me/?text={urllib.parse.quote(whatsapp_message)}"
                
                whatsapp_links.append({
                    'supplier': supplier,
                    'url': wa_url
                })
                
            except Exception as e:
                print(f"[ERROR] Error generando PDF para {supplier}: {e}")
                continue
        
        if not generated_pdfs:
            return jsonify({
                'success': False,
                'error': 'No se pudieron generar los PDFs'
            }), 500
        
        return jsonify({
            'success': True,
            'message': f'{len(generated_pdfs)} PDF(s) generado(s) exitosamente',
            'pdfs': generated_pdfs,
            'whatsapp_links': whatsapp_links,
            'total_suppliers': len(suppliers)
        })
        
    except Exception as e:
        print(f"[ERROR] Error en generate_pdfs: {e}")
        return jsonify({'success': False, 'error': 'Error interno del servidor'}), 500


@app.route("/business/save", methods=["POST"])
def save_business():
    print("🚀 Ruta /business/save llamada")

    data = request.json
    current_user = session.get("user")
    current_role = session.get("role")

    if not current_user:
        return jsonify({"success": False, "error": "Usuario no autenticado. Por favor, inicia sesion."})

    if not data:
        return jsonify({"success": False, "error": "No se recibieron datos"})

    conn = None
    cursor = None
    try:
        print("🔌 Intentando conectar a BD...")
        conn = get_connection()                      # 👉 conexion NUEVA por request
        conn.autocommit = False                      # manejamos nosotros el commit

        # 👉 usar cursor buffered para evitar resultados pendientes
        cursor = conn.cursor(buffered=True, dictionary=True)
        print("✅ Conexion exitosa")

        # 1) Verificar usuario (y CONSUMIR resultados)
        cursor.execute("SELECT username FROM usuarios WHERE username = %s", (current_user,))
        _ = cursor.fetchone()                        # fetch para vaciar el resultset

        if not _:
            return jsonify({
                "success": False,
                "error": f"El usuario '{current_user}' no existe en la base de datos. Contacta al administrador."
            })

        # 2) Actualizar datos
        sql = """
            UPDATE usuarios 
               SET business_name = %s,
                   comercio      = %s,
                   address       = %s,
                   phone         = %s,
                   email         = %s
             WHERE username      = %s
        """
        values = (
            data.get("businessName"),
            data.get("contactPerson"),
            data.get("businessAddress"),
            data.get("businessPhone"),
            data.get("businessEmail"),
            current_user
        )
        cursor.execute(sql, values)
        if cursor.rowcount == 0:
            conn.rollback()
            return jsonify({"success": False, "error": f"No se pudo actualizar el usuario '{current_user}'"})

        conn.commit()                                # ✅ commit de escritura

        # 3) Verificar (leer y consumir resultados)
        cursor.execute(
            "SELECT business_name, comercio, address, phone, email FROM usuarios WHERE username = %s",
            (current_user,)
        )
        updated = cursor.fetchone()                  # <— consumir resultado

        return jsonify({
            "success": True,
            "message": f"Datos del negocio actualizados correctamente para {current_user}",
            "user": current_user,
            "data": updated
        })

    except Exception as e:
        if conn:
            try: conn.rollback()
            except: pass
        print("❌ ERROR:", e)
        return jsonify({"success": False, "error": str(e)})
    finally:
        try:
            if cursor: cursor.close()
        finally:
            if conn: conn.close()                   



@app.route('/business/info', methods=['GET', 'POST'])
@login_required
def business_info_route():
    """Guardar/leer datos del comercio del usuario."""
    user = session['user']
    if request.method == 'POST':
        data = request.get_json()
        business_data[user] = {
            'business_name': data.get('business_name', ''),
            'address': data.get('address', ''),
            'phone': data.get('phone', ''),
            'email': data.get('email', '')
        }
        return jsonify({'success': True, 'message': 'Informacion guardada'})
    return jsonify(business_data.get(user, {}))





@app.route('/clear')
def clear_lists():
    """Limpiar todas las listas cargadas"""
    global price_lists
    price_lists = {}
    return jsonify({'success': True, 'message': 'Todas las listas han sido eliminadas'})

# ⬇️ reemplazar la ruta /remove_list completa por esta
@app.route('/remove_list/<path:supplier>', methods=['GET', 'DELETE'])
@login_required
def remove_list(supplier):
    """Eliminar una lista/proveedor desde la BD (productos y opcionalmente proveedores_config)."""
    # Decodificar por si viene con %20, etc.
    supplier_raw = supplier
    supplier = urllib.parse.unquote(supplier_raw)

    conn = get_connection()
    try:
        cur = conn.cursor()

        # 1) borrar productos (intento exacto)
        cur.execute("DELETE FROM productos WHERE proveedor = %s", (supplier,))
        deleted_prod = cur.rowcount

        # 1.b) si no borro nada, intenta con normalizacion (espacios/case)
        if deleted_prod == 0:
            cur.execute("""
                DELETE FROM productos 
                WHERE LOWER(TRIM(proveedor)) = LOWER(TRIM(%s))
            """, (supplier,))
            deleted_prod = cur.rowcount

        # 2) borrar ficha del proveedor (opcional; descomentalo si queres)  
        cur.execute("DELETE FROM proveedores_config WHERE proveedor = %s", (supplier,))
        deleted_cfg = cur.rowcount
        if deleted_cfg == 0:
             cur.execute("""
                 DELETE FROM proveedores_config 
                 WHERE LOWER(TRIM(proveedor)) = LOWER(TRIM(%s))
             """, (supplier,))
             deleted_cfg = cur.rowcount
        deleted_cfg = 0  # si lo dejas comentado arriba

        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': f'Error eliminando la lista: {e}'}), 500
    finally:
        conn.close()

    # Limpiar memoria si existiera
    try:
        if supplier in price_lists:
            del price_lists[supplier]
    except Exception:
        pass

    if deleted_prod > 0 or deleted_cfg > 0:
        return jsonify({
            'success': True,
            'message': f'Eliminado {supplier}: {deleted_prod} productos'
                       + (f' y {deleted_cfg} ficha(s) proveedor' if deleted_cfg else '')
        })
    else:
        return jsonify({'success': False, 'message': 'Lista no encontrada'})

@app.route('/ai/suggest')
def ai_suggest():
    """Endpoint preparado para sugerencias de IA"""
    query = request.args.get('q', '')
    
    # Por ahora retorna sugerencias basicas
    suggestions = []
    
    if price_lists and query:
        # Buscar productos similares para sugerir
        all_products = []
        for data in price_lists.values():
            for product in data['products']:
                all_products.append(product['product'].lower())
        
        # Sugerencias simples por ahora
        suggestions = [p for p in set(all_products) if query.lower() in p][:5]
    
    return jsonify({
        'suggestions': suggestions,
        'ai_ready': False,  # Cambiar a True cuando se implemente IA
        'message': 'Funcionalidad de IA lista para implementar'
    })

@app.route('/debug_file/<supplier>')
def debug_file_info(supplier):
    """Obtener informacion de debug detallada de un archivo especifico"""
    if supplier in price_lists:
        return jsonify({
            'supplier': supplier,
            'data': price_lists[supplier],
            'sample_products': price_lists[supplier]['products'][:5]  # Primeros 5 productos como muestra
        })
    else:
        return jsonify({
            'error': 'Proveedor no encontrado',
            'available_suppliers': list(price_lists.keys())
        })

@app.route('/cleanup')
def cleanup_temp_files():
    """Limpiar archivos temporales que no se pudieron eliminar"""
    try:
        upload_folder = app.config['UPLOAD_FOLDER']
        if os.path.exists(upload_folder):
            files_removed = 0
            for filename in os.listdir(upload_folder):
                filepath = os.path.join(upload_folder, filename)
                try:
                    os.remove(filepath)
                    files_removed += 1
                except:
                    pass
            
            return jsonify({
                'success': True,
                'message': f'{files_removed} archivos temporales eliminados'
            })
        else:
            return jsonify({
                'success': True,
                'message': 'No hay archivos temporales para limpiar'
            })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error limpiando archivos: {str(e)}'
        })
    
def get_connection():
    return mysql.connector.connect(
        host="31.97.151.160",
        user="miusuario",
        password="M1password!",
        port=3306,
        database="sys",
        autocommit=False
    )

def upsert_proveedor(nombre: str, direccion: str | None, telefono: str | None, email: str | None = None):
    """Crea o actualiza un proveedor con su direccion, telefono y email."""
    if not nombre:
        return
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO proveedores_config (proveedor, direccion, telefono, email)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE 
                direccion=VALUES(direccion), 
                telefono=VALUES(telefono),
                email=VALUES(email)
        """, (nombre, direccion or '', telefono or '', email or ''))
        conn.commit()
    finally:
        conn.close()

def get_proveedor_info(nombre: str) -> dict:
    """Devuelve {'proveedor','direccion','telefono','email'} para el proveedor o valores vacios si no existe."""
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT proveedor, direccion, telefono, email FROM proveedores_config WHERE proveedor=%s", (nombre,))
        row = cur.fetchone()
        if not row:
            return {'proveedor': nombre, 'direccion': '', 'telefono': '', 'email': ''}
        
        # Convertir None a string vacio
        return {
            'proveedor': row.get('proveedor') or nombre,
            'direccion': row.get('direccion') or '',
            'telefono': row.get('telefono') or '',
            'email': row.get('email') or ''
        }
    finally:
        conn.close()

def get_supplier_phone(nombre: str) -> str:
    """
    Devuelve el telefono del proveedor priorizando la BD (proveedores_config).
    Si no existe o esta vacio, usa el respaldo SUPPLIER_PHONES.
    """
    try:
        prov = get_proveedor_info(nombre) or {}
        telefono_db = (prov.get('telefono') or '').strip()
        if telefono_db:
            return telefono_db
    except Exception:
        pass
    return (SUPPLIER_PHONES.get(nombre, '') or '').strip()

import re
from datetime import datetime

def normalizar_texto(s: str) -> str:
    return re.sub(r'\s+', ' ', str(s).strip().lower())

def guardar_productos_en_bd(proveedor: str, productos: list[dict]):
    """
    - Si el producto YA EXISTE del MISMO proveedor: lo SOBREESCRIBE (actualiza)
    - Si el producto es de OTRO proveedor: crea una NUEVA FILA con nuevo ID
    productos: [{'product': str, 'price': float, ...}, ...]
    """
    conn = get_connection()
    try:
        conn.start_transaction()
        cur = conn.cursor()

        # 1) Borrar productos anteriores de este proveedor
        cur.execute("DELETE FROM productos WHERE proveedor = %s", (proveedor,))
        productos_eliminados = cur.rowcount

        # 2) Insertar/actualizar productos
        filas = []
        productos_procesados = 0
        
        for p in productos:
            try:
                prod = str(p['product']).strip()
                precio = float(p['price'])
                
                if not prod:
                    continue
                    
                filas.append((
                    proveedor,
                    prod,
                    normalizar_texto(prod),
                    precio
                ))
                productos_procesados += 1
                
            except (ValueError, KeyError) as e:
                print(f"[WARNING] Error procesando producto {p}: {e}")
                continue

        # Usar UPSERT para manejar duplicados
        if filas:
            cur.executemany(
                """INSERT INTO productos (proveedor, producto, producto_normalizado, precio, actualizado_a)
                   VALUES (%s, %s, %s, %s, NOW())
                   ON DUPLICATE KEY UPDATE
                   producto_normalizado = VALUES(producto_normalizado),
                   precio = VALUES(precio),
                   actualizado_a = NOW()""",
                filas
            )

        conn.commit()
        print(f"[DB] {proveedor}: {productos_eliminados} eliminados, {len(filas)} productos procesados (insertados/actualizados)")
        
    except Exception as e:
        conn.rollback()
        print(f"[DB ERROR] guardar_productos_en_bd para {proveedor}: {e}")
        raise
    finally:
        conn.close()

def buscar_productos_bd(q: str, limite: int = 300):
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        like = f"%{normalizar_texto(q)}%"
        cur.execute(
            """SELECT proveedor, producto, precio, actualizado_a
               FROM productos
               WHERE producto_normalizado LIKE %s
               ORDER BY precio ASC
               LIMIT %s""",
            (like, limite)
        )
        return cur.fetchall()
    except Exception as e:
        print("[DB ERROR] buscar_productos_bd:", e)
        return []
    finally:
        conn.close()

def has_complete_business_data(username):
    """Verifica si el usuario tiene datos completos del negocio en la BD"""
    business_data = get_user_business_data(username)
    return all([
        business_data.get('business_name', '').strip(),
        business_data.get('address', '').strip(),
        business_data.get('phone', '').strip()
    ])
# Verifica usuario en la DB
def get_user(username, password):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM usuarios WHERE username=%s AND password=%s", (username, password))
    user = cursor.fetchone()
    conn.close()
    return user


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        # Admin hardcodeado → va a index.html
        if username == "admintios" and password == "Papagianagerli1":
            session["user"] = username
            session["role"] = "admin"
            return redirect(url_for("index_pagina"))

        # Resto de usuarios → valida en DB y va a indexusuario.html
        user = get_user(username, password)
        if user:
            session["user"] = username
            session["role"] = "user"
            return redirect(url_for("index_usuario"))

        flash("Usuario o contraseña incorrectos", "error")
        return redirect(url_for("login"))

    return render_template("login.html")





@app.route("/indexusuario")
@login_required           # <-- cualquier logueado, pero si es admin lo mando a /index
def index_usuario():
    if session.get("role") == "admin":
        return redirect(url_for("index_pagina"))
    return render_template("indexusuario.html", user=session["user"])
 


@app.route("/index")
def index_pagina():
    if "user" not in session:
        return redirect(url_for("login"))
    if session.get("role") != "admin":
        return redirect(url_for("index_usuario"))
    return render_template("index.html", user=session["user"])


@app.errorhandler(404)
def _404_to_login(e):
    # no interceptes estaticos ni el propio /login
    if request.path.startswith("/static/") or request.path == "/login":
        return e, 404
    return redirect(url_for("login"))


@app.route("/logout", methods=["POST"])
def logout():
    session.pop("user", None)              # borra la sesion
    flash("Sesion cerrada", "info")        # opcional: mensaje
    return redirect(url_for("login"))      # redirige al login


@app.before_request
def _redirect_root_to_login():
    if request.path == "/":
        return redirect(url_for("login"))
 
@app.route('/business/check', methods=['GET'])
@login_required
def check_business_data():
    """Verifica si el usuario tiene datos completos del negocio"""
    user = session['user']
    has_data = has_complete_business_data(user)
    business_data = get_user_business_data(user) if has_data else {}
    
    return jsonify({
        'has_complete_data': has_data,
        'data': business_data,
        'required_fields': ['business_name', 'address', 'phone']
    })
@app.route('/business/setup', methods=['GET', 'POST'])
@login_required
def business_setup():
    """Modal/pagina para configurar datos del negocio antes de generar PDFs"""
    user = session['user']
    if request.method == 'POST':
        data = request.get_json()
        business_data[user] = {
            'business_name': data.get('business_name', '').strip(),
            'address': data.get('address', '').strip(),
            'phone': data.get('phone', '').strip(),
            'email': data.get('email', '').strip(),
            'contact_person': data.get('contact_person', '').strip()
        }
        return jsonify({'success': True, 'message': 'Datos del negocio guardados'})
    
    return jsonify(business_data.get(user, {}))

def generate_pdf_for_supplier(supplier_name, items, business_data, filename):
    """
    Genera el PDF del pedido para un proveedor.
    - supplier_name: str (nombre EXACTO del proveedor como esta en la BD)
    - items: lista de dicts con keys: product, quantity, price
    - business_data: dict con keys: business_name, contact_person, address, phone, email
    - filename: nombre de archivo de salida (str)
    """
    # Traer datos del proveedor desde BD (con fallbacks)
    prov = get_proveedor_info(supplier_name) or {}
    print(f"Debug - Proveedor: {supplier_name}")
    print(f"Debug - Datos obtenidos: {prov}")
    print(f"Debug - Tipo direccion: {type(prov.get('direccion'))}, valor: '{prov.get('direccion')}'")
    print(f"Debug - Tipo telefono: {type(prov.get('telefono'))}, valor: '{prov.get('telefono')}'")
    supplier_address = (prov.get('direccion') or '').strip() or 'Ubicacion no especificada'
    supplier_phone   = (prov.get('telefono')  or '').strip() or 'Telefono no disponible'

    pdf_path = os.path.join(app.config['PDFS_FOLDER'], filename)
    doc = SimpleDocTemplate(pdf_path, pagesize=letter, topMargin=50, bottomMargin=50)

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=18,
        spaceAfter=30,
        textColor=colors.darkblue,
        alignment=1,
        fontName='Helvetica-Bold'
    )
    header_style = ParagraphStyle(
        'HeaderStyle',
        parent=styles['Heading2'],
        fontSize=14,
        spaceAfter=15,
        textColor=colors.black,
        fontName='Helvetica-Bold'
    )

    story = []

    # ---- Encabezado
    story.append(Paragraph(f"PEDIDO PARA {supplier_name.upper()}", title_style))
    story.append(Spacer(1, 20))

    # ---- Datos del comercio
    story.append(Paragraph("DATOS DEL COMERCIO", header_style))
    business_info_text = f"""
    <b>Comercio:</b> {business_data.get('business_name', 'N/A')}<br/>
    <b>Persona de Contacto:</b> {business_data.get('contact_person', 'N/A')}<br/>
    <b>Direccion de Entrega:</b> {business_data.get('address', 'N/A')}<br/>
    <b>Telefono:</b> {business_data.get('phone', 'N/A')}<br/>
    <b>Email:</b> {business_data.get('email', 'N/A')}<br/>
    """
    story.append(Paragraph(business_info_text, styles['Normal']))
    story.append(Spacer(1, 25))

    # ---- Fecha y Nº de pedido
    story.append(Paragraph(f"<b>Fecha del Pedido:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}", styles['Normal']))
    story.append(Paragraph(f"<b>Numero de Pedido:</b> {datetime.now().strftime('%Y%m%d%H%M%S')}", styles['Normal']))
    story.append(Spacer(1, 25))

    # ---- Detalle de productos
    story.append(Paragraph("DETALLE DEL PEDIDO", header_style))

    table_data = [['Producto', 'Cantidad', 'Precio Unitario', 'Subtotal']]
    total = 0.0
    for item in items:
        quantity = int(item.get('quantity', 0) or 0)
        price = float(item.get('price', 0) or 0.0)
        subtotal = quantity * price
        total += subtotal
        # Usamos Paragraph para permitir wrap de nombres largos
        table_data.append([
            Paragraph(str(item.get('product', '')), styles['Normal']),
            str(quantity),
            f"${price:,.2f}",
            f"${subtotal:,.2f}"
        ])

    # Total
    table_data.append([
        '',
        '',
        Paragraph('<b>TOTAL:</b>', styles['Normal']),
        Paragraph(f'<b>${total:,.2f}</b>', styles['Normal'])
    ])

    table = Table(table_data, colWidths=[3.5*inch, 0.8*inch, 1.2*inch, 1.2*inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.navy),
        ('TEXTCOLOR',  (0, 0), (-1, 0), colors.whitesmoke),
        ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0, 0), (-1, 0), 11),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('TOPPADDING',    (0, 0), (-1, 0), 12),

        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),

        # Alinear nombre de producto a la izquierda, resto centrado
        ('ALIGN', (0, 1), (0, -2), 'LEFT'),
        ('ALIGN', (1, 1), (-1, -2), 'CENTER'),

        ('BACKGROUND', (0, 1), (-1, -2), colors.beige),
        ('BACKGROUND', (0, -1), (-1, -1), colors.lightblue),
        ('FONTNAME',   (0, -1), (-1, -1), 'Helvetica-Bold'),
    ]))
    story.append(table)
    story.append(Spacer(1, 30))

    # ---- Datos del proveedor (desde BD)
    story.append(Paragraph("DATOS DEL PROVEEDOR", header_style))
    supplier_info_text = f"""
    <b>Proveedor:</b> {supplier_name}<br/>
    <b>Direccion:</b> {supplier_address}<br/>
    <b>Telefono:</b> {supplier_phone}<br/>

    """
    story.append(Paragraph(supplier_info_text, styles['Normal']))
    story.append(Spacer(1, 20))

    # ---- Notas
    story.append(Paragraph("NOTAS", header_style))
    notes_text = """
    • Por favor confirme disponibilidad de todos los productos<br/>
    • Solicite tiempo estimado de entrega<br/>
    • Verifique condiciones de pago<br/>
    • Este pedido esta sujeto a confirmacion
    """
    story.append(Paragraph(notes_text, styles['Normal']))

    doc.build(story)
    return pdf_path


@app.route("/business/get", methods=["GET"])
def get_business():
    """Obtiene los datos del negocio del usuario actual"""
    
    current_user = session.get('user')  # ← Corregido
    
    if not current_user:
        return jsonify({"success": False, "error": "Usuario no autenticado"})
    
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)  # Para obtener resultados como diccionario
        
        sql = """
        SELECT business_name, comercio, address, phone, email 
        FROM usuarios 
        WHERE username = %s
        """
        
        cursor.execute(sql, (current_user,))
        business_data = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        if business_data:
            return jsonify({
                "success": True, 
                "data": business_data,
                "message": "Datos del negocio obtenidos correctamente"
            })
        else:
            return jsonify({
                "success": False, 
                "message": "No se encontraron datos del negocio para este usuario"
            })
            
    except Exception as e:
        print("❌ ERROR obteniendo datos del negocio:", str(e))
        return jsonify({"success": False, "error": str(e)})


def create_whatsapp_message(supplier_name, items, business_data, total):
    contact_person = business_data.get('contact_person', business_data.get('business_name', 'Cliente'))
    prov = get_proveedor_info(supplier_name)
    prov_dir = prov.get('direccion', '')
    prov_tel = prov.get('telefono', '')

    message = f""" *NUEVO PEDIDO*

 *De:* {contact_person}
 *Comercio:* {business_data.get('business_name', 'N/A')}
- *Datos de Entrega:*
-  Direccion: {business_data.get('address', 'N/A')}
- Telefono: {business_data.get('phone', 'N/A')}
- Email: {business_data.get('email', 'N/A')}

 *Proveedor:* {supplier_name}
- Direccion: {prov_dir or 'N/D'}
- Telefono: {prov_tel or 'N/D'}

*Productos Solicitados:*
"""
    # ... resto tal cual

    
    for i, item in enumerate(items, 1):
        subtotal = item['quantity'] * item['price']
        message += f"{i}. *{item['product']}*\n"
        message += f"   - Cantidad: {item['quantity']}\n"
        message += f"   - Precio: ${item['price']:,.2f} c/u\n"
        message += f"   - Subtotal: ${subtotal:,.2f}\n\n"

    message += f"- *TOTAL DEL PEDIDO: ${total:,.2f}*\n\n"
    message += f"- *Fecha:* {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
    message += f"- *Nº Pedido:* {datetime.now().strftime('%Y%m%d%H%M%S')}\n\n"
    
    message += "*Por favor confirme:*\n"
    message += "- Disponibilidad de productos\n"
    message += "- Tiempo de entrega\n"
    message += "- Condiciones de pago\n"
    message += "- Cualquier modificacion necesaria\n\n"
    
    message += "¡Gracias por su atencion! "
    
    return message

def _normalize_phone(raw_phone: str) -> str:
    """Deja solo digitos para wa.me."""
    return re.sub(r'\D+', '', raw_phone or '')



def get_user_business_data(username):
    """Obtiene los datos completos del negocio del usuario desde la BD"""
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT business_name, comercio as contact_person, address, phone, email 
            FROM usuarios 
            WHERE username = %s
        """, (username,))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if result:
            # Asegurar que los campos requeridos existen
            return {
                'business_name': result.get('business_name', '').strip(),
                'contact_person': result.get('contact_person', '').strip(),
                'address': result.get('address', '').strip(),
                'phone': result.get('phone', '').strip(),
                'email': result.get('email', '').strip()
            }
        return {}
        
    except Exception as e:
        print(f"Error obteniendo datos del negocio: {e}")
        return {}

# === ENDPOINT PARA VALIDAR DATOS DEL NEGOCIO ===
@app.route('/business/validate', methods=['POST'])
@login_required
def validate_business_data():
    """Valida y guarda los datos del negocio"""
    user = session['user']
    data = request.get_json()
    
    # Validaciones
    errors = []
    if not data.get('business_name', '').strip():
        errors.append('Nombre del comercio es obligatorio')
    if not data.get('address', '').strip():
        errors.append('Direccion es obligatoria')
    if not data.get('phone', '').strip():
        errors.append('Telefono es obligatorio')
    
    if errors:
        return jsonify({
            'success': False,
            'errors': errors,
            'message': 'Corrija los siguientes errores'
        }), 400
    
    # Guardar datos
    business_data[user] = {
        'business_name': data.get('business_name', '').strip(),
        'address': data.get('address', '').strip(), 
        'phone': data.get('phone', '').strip(),
        'email': data.get('email', '').strip(),
        'contact_person': data.get('contact_person', '').strip()
    }
    
    return jsonify({
        'success': True,
        'message': 'Datos del negocio guardados correctamente',
        'data': business_data[user]
    })

# === ENDPOINT MEJORADO PARA DESCARGAR PDF ===
@app.route('/download_pdf/<filename>')
@login_required
def download_pdf(filename):
    """Descarga un PDF generado"""
    try:
        pdf_path = os.path.join(app.config['PDFS_FOLDER'], filename)
        
        if not os.path.exists(pdf_path):
            return jsonify({'error': 'Archivo no encontrado'}), 404
            
        # Verificar que el archivo pertenece al usuario actual (opcional)
        user = session['user']
        safe_user = user.replace(' ', '_').replace('/', '_')
        if safe_user not in filename:
            return jsonify({'error': 'Acceso denegado'}), 403
            
        return send_file(
            pdf_path,
            as_attachment=True,
            download_name=filename,
            mimetype='application/pdf'
        )
    except Exception as e:
        return jsonify({'error': f'Error descargando archivo: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
else:
    app.debug = False