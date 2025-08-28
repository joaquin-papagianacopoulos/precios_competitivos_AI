from flask import Flask, render_template, request, jsonify, send_from_directory, session, flash, redirect, url_for
import pandas as pd
import os
from werkzeug.utils import secure_filename
import json
from datetime import datetime
import re
import mysql.connector
from functools import wraps
from flask import redirect, url_for, session, request 

# === NUEVOS IMPORTS PARA CARRITO/PDF/WA ===
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
import urllib.parse
from flask import send_file



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
user_carts = {}     # carrito por usuario logueado
business_data = {}  # datos del comercio por usuario


# Ubicaciones por defecto de mayoristas
DEFAULT_LOCATIONS = {
    'Mayorista Central': 'Av. Corrientes 1234, Buenos Aires, Argentina',
    'Distribuidora Norte': 'San Martín 567, Córdoba, Argentina',
    'Comercial Sur': 'Pellegrini 890, Rosario, Argentina',
    'Proveedor Express': 'Florida 456, Buenos Aires, Argentina'
}

# === TELÉFONOS PARA WHATSAPP (dummy/ejemplo) ===
SUPPLIER_PHONES = {
    'Gallesur': '+541156649404',
    'Distribuidora Norte': '+5491123456790',
    'Comercial Sur': '+5491123456791',
    'Proveedor Express': '+5491123456792'
}


class PriceListProcessor:
    def __init__(self):
        self.possible_product_columns = ['producto', 'descripcion', 'item', 'nombre', 'description', 'product', 'nombre del articulo', 'nombre del producto', 'articulo']
        self.possible_price_columns = ['precio', 'price', 'costo', 'valor', 'cost', 'amount', 'Importe c/IVA', 'importe', 'efectivo', 'unitario', 'pcio', 'prcio', 'prcio', 'precio unitario', 'precio unit', 'p.unit', 'pu', 'precio u', 'lista', 'precio lista', 'tarifa', 'neto']
    
    def find_column_index(self, df, possible_names):
        """Encuentra el índice de la columna basándose en nombres posibles"""
        columns_lower = [str(col).lower().strip() for col in df.columns]
        
        for name in possible_names:
            for i, col in enumerate(columns_lower):
                if name in col:
                    return i
        return None
    
    def find_column_in_first_rows(self, df, possible_names, max_rows=8):
        """
        Busca columnas de producto/precio en las primeras filas del DataFrame
        Útil cuando los headers reales están en filas posteriores
        """
        print(f"🔍 Buscando columnas en las primeras {max_rows} filas...")
        
        # Limitar a las primeras max_rows filas disponibles
        search_rows = min(max_rows, len(df))
        
        for row_idx in range(search_rows):
            print(f"📋 Analizando fila {row_idx}: {list(df.iloc[row_idx].values)}")
            
            # Convertir los valores de la fila a strings y limpiarlos
            row_values = [str(val).lower().strip() if pd.notna(val) else '' for val in df.iloc[row_idx].values]
            
            # Buscar coincidencias con nombres posibles
            found_columns = {}
            for col_idx, cell_value in enumerate(row_values):
                if cell_value:  # Solo si la celda no está vacía
                    for name in possible_names:
                        if name in cell_value:
                            found_columns[col_idx] = (cell_value, name)
                            print(f"✅ Encontrado '{name}' en columna {col_idx}, fila {row_idx}: '{cell_value}'")
            
            if found_columns:
                return row_idx, found_columns
        
        return None, {}
    
    def clean_price(self, price_str):
        """Limpia y convierte string de precio a float - SOLO acepta valores que realmente parezcan precios"""
        if pd.isna(price_str):
            return None
        
        # Convertir a string si no lo es
        price_str = str(price_str).strip()
        
        # RECHAZO INMEDIATO: Si el string contiene letras (excepto símbolos de moneda), no es un precio
        if re.search(r'[a-zA-Z]', price_str):
            return None
        
        # RECHAZO INMEDIATO: Si contiene "X" o "x" (indicador de cantidad), no es un precio
        if 'X' in price_str.upper():
            return None
            
        # RECHAZO INMEDIATO: Si es solo un número sin formato de precio (sin $, puntos, comas)
        # y es menor a 3 dígitos, probablemente es cantidad, no precio
        if re.match(r'^\d{1,2}$', price_str):
            return None
        
        # Debe tener al menos un dígito
        if not re.search(r'\d', price_str):
            return None
        
        # Remover símbolos de moneda al inicio
        price_str = re.sub(r'^[\$€£¥₹₩₽¢]+\s*', '', price_str)
        
        # Remover caracteres no numéricos excepto puntos y comas
        clean_price = re.sub(r'[^\d.,]', '', price_str)
        
        # Si después de limpiar no queda nada o solo caracteres, rechazar
        if not clean_price or clean_price in ['.', ',', '.,', ',.']:
            return None
        
        # Si es un solo dígito después de limpiar, probablemente no es precio
        if len(clean_price) == 1:
            return None
            
        # Manejar formato argentino (coma como decimal)
        if ',' in clean_price and '.' in clean_price:
            # Si tiene ambos, asumir que el punto es separador de miles
            clean_price = clean_price.replace('.', '').replace(',', '.')
        elif ',' in clean_price:
            # Si solo tiene coma, puede ser decimal
            clean_price = clean_price.replace(',', '.')
        
        try:
            price_value = float(clean_price)
            # Validar que el precio sea razonable 
            # Precios muy bajos probablemente son cantidades, no precios
            if price_value <= 0.10 or price_value > 999999:
                return None
            return price_value
        except:
            return None
    
    def process_excel_file(self, file_path, supplier_name):
        """Procesa un archivo Excel y extrae productos y precios"""
        excel_file = None
        debug_info = []
        
        try:
            print(f"🔍 Procesando archivo: {file_path}")
            
            # Leer todas las hojas del Excel
            excel_file = pd.ExcelFile(file_path)
            all_products = []
            
            print(f"📊 Hojas encontradas en {supplier_name}: {excel_file.sheet_names}")
            debug_info.append(f"Hojas: {', '.join(excel_file.sheet_names)}")
            
            for sheet_name in excel_file.sheet_names:
                print(f"📋 Procesando hoja: {sheet_name}")
                
                try:
                    # Leer sin especificar header para tener acceso a todas las filas
                    df = pd.read_excel(excel_file, sheet_name=sheet_name, header=None)
                    
                    if df.empty:
                        print(f"⚠️ Hoja {sheet_name} está vacía")
                        debug_info.append(f"Hoja {sheet_name}: vacía")
                        continue
                    
                    print(f"📏 Dimensiones de {sheet_name}: {df.shape}")
                    debug_info.append(f"Hoja {sheet_name}: {df.shape[0]} filas, {df.shape[1]} columnas")
                    
                    # Buscar primero en headers tradicionales
                    df_with_header = pd.read_excel(excel_file, sheet_name=sheet_name)
                    product_col_idx = self.find_column_index(df_with_header, self.possible_product_columns)
                    price_col_idx = self.find_column_index(df_with_header, self.possible_price_columns)
                    
                    header_row = 0  # Por defecto, asumir que la primera fila es el header
                    
                    # Si no se encontraron columnas en headers, buscar en las primeras 8 filas
                    if product_col_idx is None or price_col_idx is None:
                        print("🔍 No se encontraron columnas en headers, buscando en primeras 8 filas...")
                        
                        # Buscar columnas de producto
                        if product_col_idx is None:
                            header_row_product, found_product_cols = self.find_column_in_first_rows(
                                df, self.possible_product_columns
                            )
                            if found_product_cols:
                                product_col_idx = list(found_product_cols.keys())[0]
                                header_row = max(header_row, header_row_product)
                                print(f"✅ Columna de producto encontrada en fila {header_row_product}, columna {product_col_idx}")
                        
                        # Buscar columnas de precio
                        if price_col_idx is None:
                            header_row_price, found_price_cols = self.find_column_in_first_rows(
                                df, self.possible_price_columns
                            )
                            if found_price_cols:
                                price_col_idx = list(found_price_cols.keys())[0]
                                header_row = max(header_row, header_row_price)
                                print(f"✅ Columna de precio encontrada en fila {header_row_price}, columna {price_col_idx}")
                    
                    if product_col_idx is None:
                        print(f"❌ No se encontró columna de producto en {sheet_name}")
                        debug_info.append(f"Hoja {sheet_name}: No se encontró columna de producto")
                        continue
                        
                    if price_col_idx is None:
                        print(f"❌ No se encontró columna de precio en {sheet_name}")
                        print(f"🔍 Columnas disponibles: {list(df_with_header.columns)}")
                        print(f"📋 Valores de la fila header ({header_row}): {list(df.iloc[header_row].values) if header_row < len(df) else 'Fila no disponible'}")
                        debug_info.append(f"Hoja {sheet_name}: No se encontró columna de precio. Columnas disponibles: {list(df_with_header.columns)}")
                        
                        # PREVENIR ERROR: No permitir que use la misma columna para producto y precio
                        print(f"⚠️ EVITANDO ERROR: No se puede usar la misma columna para producto y precio")
                        continue
                    
                    # VALIDACIÓN CRÍTICA: Verificar que producto y precio son columnas diferentes
                    if product_col_idx == price_col_idx:
                        print(f"❌ ERROR CRÍTICO: La misma columna ({product_col_idx}) se detectó para producto Y precio")
                        print(f"🔍 Esto indica que no se encontró una columna de precio válida")
                        print(f"📋 Columnas disponibles: {list(df_with_header.columns)}")
                        debug_info.append(f"Hoja {sheet_name}: ERROR - misma columna para producto y precio (col {product_col_idx})")
                        continue
                    
                    # Crear DataFrame con el header correcto
                    if header_row > 0:
                        # Si encontramos headers en filas posteriores, usar esa fila como header
                        df_processed = pd.read_excel(excel_file, sheet_name=sheet_name, header=header_row)
                        # Ajustar los índices de columna ya que el DataFrame cambió
                        if len(df_processed.columns) > product_col_idx:
                            product_col = df_processed.columns[product_col_idx]
                        else:
                            product_col = product_col_idx
                        
                        if len(df_processed.columns) > price_col_idx:
                            price_col = df_processed.columns[price_col_idx]
                        else:
                            price_col = price_col_idx
                    else:
                        df_processed = df_with_header
                        product_col = df_processed.columns[product_col_idx]
                        price_col = df_processed.columns[price_col_idx]
                    
                    print(f"✅ Columnas detectadas - Producto: '{product_col}' (col {product_col_idx}), Precio: '{price_col}' (col {price_col_idx})")
                    print(f"📍 Header detectado en fila: {header_row}")
                    debug_info.append(f"Hoja {sheet_name}: Producto='{product_col}', Precio='{price_col}', Header en fila {header_row}")
                    
                    # Procesar cada fila
                    products_in_sheet = 0
                    skipped_invalid_prices = 0
                    
                    for idx, row in df_processed.iterrows():
                        try:
                            product = row[product_col]
                            price_raw = row[price_col]
                            
                            # DEBUG: Mostrar exactamente qué está leyendo
                            if products_in_sheet < 3:  # Solo para los primeros 3 productos
                                print(f"🔍 DEBUG Fila {idx}:")
                                print(f"   Producto (columna '{product_col}'): '{product}'")
                                print(f"   Precio RAW (columna '{price_col}'): '{price_raw}' (tipo: {type(price_raw)})")
                            
                            # Solo procesar si hay un producto válido
                            if pd.notna(product) and str(product).strip():
                                price = self.clean_price(price_raw)
                                
                                # Debug detallado para los primeros casos
                                if products_in_sheet < 3:
                                    print(f"   Precio PROCESADO: {price}")
                                    if price is None:
                                        print(f"   ❌ PRECIO RECHAZADO: '{price_raw}' no es un precio válido")
                                    else:
                                        print(f"   ✅ PRECIO ACEPTADO: {price}")
                                
                                if price is not None and price > 0:

                                    product_info = {
                                        'product': str(product).strip(),
                                        'price': price,
                                        'supplier': supplier_name,
                                        'sheet': sheet_name,
                                        'location': DEFAULT_LOCATIONS.get(supplier_name, 'Buenos Aires, Argentina'),
                                        'id': f"{supplier_name}_{sheet_name}_{idx}_{products_in_sheet}"
                                    }


                                    all_products.append(product_info)
                                    products_in_sheet += 1
                                elif pd.notna(price_raw):
                                    skipped_invalid_prices += 1
                                    
                        except Exception as row_error:
                            print(f"❌ Error procesando fila {idx}: {row_error}")
                            continue  # Saltar filas con errores
                    
                    print(f"✅ Productos válidos en {sheet_name}: {products_in_sheet}")
                    if skipped_invalid_prices > 0:
                        print(f"⚠️ Precios inválidos omitidos: {skipped_invalid_prices}")
                    debug_info.append(f"Hoja {sheet_name}: {products_in_sheet} productos válidos, {skipped_invalid_prices} precios inválidos omitidos")
                    
                except Exception as sheet_error:
                    print(f"❌ Error procesando hoja {sheet_name}: {str(sheet_error)}")
                    debug_info.append(f"Hoja {sheet_name}: Error - {str(sheet_error)}")
                    continue
            
            print(f"🎉 Total productos procesados para {supplier_name}: {len(all_products)}")
            
            # Si no se encontraron productos, guardar info de debug
            if len(all_products) == 0:
                print(f"⚠️ DIAGNÓSTICO COMPLETO PARA {supplier_name}:")
                for info in debug_info:
                    print(f"   {info}")
            
            return all_products, debug_info
            
        except Exception as e:
            error_msg = f"Error procesando archivo {file_path}: {str(e)}"
            print(f"💥 {error_msg}")
            debug_info.append(f"Error general: {str(e)}")
            return [], debug_info
        finally:
            # Cerrar el archivo Excel explícitamente
            if excel_file is not None:
                try:
                    excel_file.close()
                except:
                    pass






# Crear instancia del procesador
processor = PriceListProcessor()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    # 1) Validaciones básicas
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'No se seleccionó archivo'}), 400

    file = request.files['file']
    supplier_name = request.form.get('supplier_name', '').strip() or 'Proveedor Sin Nombre'

    if file.filename == '':
        return jsonify({'success': False, 'message': 'No se seleccionó archivo'}), 400

    if not file.filename.lower().endswith(('.xlsx', '.xls')):
        return jsonify({'success': False, 'message': 'Tipo de archivo no soportado. Use .xlsx o .xls'}), 400

    # 2) Guardar temporalmente
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)

    try:
        file.save(filepath)

        # 3) Procesar Excel → obtener productos
        products, debug_info = processor.process_excel_file(filepath, supplier_name)

        if not products:
            return jsonify({
                'success': False,
                'message': 'No se pudieron extraer productos del archivo',
                'debug_info': debug_info
            }), 200

        # 4) (Opcional) Guardar en memoria para tu UI actual
        price_lists[supplier_name] = {
            'products': products,
            'filename': filename,
            'upload_date': datetime.now().isoformat(),
            'total_products': len(products),
            'debug_info': debug_info
        }

        # 5) Guardar en MySQL (SOBREESCRIBE por proveedor)
        try:
            guardar_productos_en_bd(supplier_name, products)
            db_status = 'OK'
        except Exception as db_err:
            db_status = f'ERROR DB: {db_err}'
            # no cortamos la respuesta para que puedas ver el error y debuggear

        # 6) Respuesta
        return jsonify({
            'success': True,
            'message': f'Archivo procesado. {len(products)} productos cargados.',
            'supplier': supplier_name,
            'total_products': len(products),
            'db_status': db_status,
            'debug_info': debug_info[:5]  # muestra solo un poco de diagnóstico
        }), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error procesando archivo: {str(e)}',
            'debug_info': [f'Error general: {str(e)}']
        }), 500

    finally:
        # 7) Limpiar archivo temporal siempre
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except:
            pass

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

@app.route('/lists')
@login_required
def get_loaded_lists():
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT proveedor,
                   COUNT(*)            AS total_products,
                   MAX(actualizado_a)  AS last_update
            FROM productos
            GROUP BY proveedor
            ORDER BY proveedor
        """)
        rows = cur.fetchall()
        lists_info = [{
            'supplier': r['proveedor'],
            'filename': r['proveedor'] + '.xlsx',  # opcional (no guardamos el real)
            'upload_date': (r['last_update'].isoformat() if r['last_update'] else None),
            'total_products': int(r['total_products'])
        } for r in rows]
        return jsonify({'lists': lists_info, 'total': len(lists_info)})
    finally:
        conn.close()



@app.route('/cart/add', methods=['POST'])
@login_required
def add_to_cart():
    """Agregar producto al carrito del usuario."""
    user = session['user']
    data = request.get_json()
    
    product_id = data.get('product_id')
    product_name = data.get('product_name')
    price = float(data.get('price', 0))
    supplier = data.get('supplier')
    quantity = int(data.get('quantity', 1))
    print(f"[DEBUG] Agregando al carrito: {product_name}, ${price}, {supplier}")  # Debug
    if user not in user_carts:
        user_carts[user] = []
    # Verificar si ya existe el producto en el carrito
    found = False
    for item in user_carts[user]:
        if item['id'] == product_id:
            item['quantity'] += quantity
            found = True
            break
    
    if not found:
        user_carts[user].append({
            'id': product_id,
            'product': product_name,
            'price': price,
            'supplier': supplier,
            'quantity': quantity
        })
    return jsonify({
        'success': True, 
        'cart_count': len(user_carts[user]),
                'message': f'{product_name} agregado al carrito'
    })

@app.route('/cart/get')
@login_required
def get_cart():
    user = session['user']
    cart = user_carts.get(user, [])
    total = sum(item['price'] * item['quantity'] for item in cart)

    # agrupar por proveedor
    suppliers = {}
    for item in cart:
        suppliers.setdefault(item['supplier'], []).append(item)

    return jsonify({
        'cart': cart,
        'total': total,
        'total_formatted': f"${total:,.2f}",
        'suppliers': suppliers
    })


@app.route('/cart/update', methods=['POST'])
@login_required
def update_cart():
    user = session['user']
    data = request.get_json()
    product_id = data.get('product_id')
    quantity = int(data.get('quantity', 1))

    cart = user_carts.get(user, [])
    for item in cart:
        if item['id'] == product_id:
            if quantity <= 0:
                cart.remove(item)
            else:
                item['quantity'] = quantity
            return jsonify({'success': True})
    return jsonify({'error': 'Producto no encontrado en el carrito'}), 404


@app.route('/cart/remove', methods=['POST'])
@login_required
def remove_from_cart():
    user = session['user']
    data = request.get_json()
    product_id = data.get('product_id')

    cart = user_carts.get(user, [])
    for item in cart:
        if item['id'] == product_id:
            cart.remove(item)
            return jsonify({'success': True})
    return jsonify({'error': 'Producto no encontrado en el carrito'}), 404


@app.route('/cart/clear')
@login_required
def clear_cart():
    user = session['user']
    user_carts[user] = []
    return jsonify({'success': True, 'message': 'Carrito limpiado'})






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
        return jsonify({'success': True, 'message': 'Información guardada'})
    return jsonify(business_data.get(user, {}))





@app.route('/clear')
def clear_lists():
    """Limpiar todas las listas cargadas"""
    global price_lists
    price_lists = {}
    return jsonify({'success': True, 'message': 'Todas las listas han sido eliminadas'})

@app.route('/remove_list/<supplier>')
def remove_list(supplier):
    """Remover una lista específica"""
    if supplier in price_lists:
        del price_lists[supplier]
        return jsonify({'success': True, 'message': f'Lista de {supplier} eliminada'})
    else:
        return jsonify({'success': False, 'message': 'Lista no encontrada'})

@app.route('/ai/suggest')
def ai_suggest():
    """Endpoint preparado para sugerencias de IA"""
    query = request.args.get('q', '')
    
    # Por ahora retorna sugerencias básicas
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
    """Obtener información de debug detallada de un archivo específico"""
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
        host="localhost",      # Cambiá si tu servidor MySQL no es local
        user="root",           # Usuario de MySQL
        password="",# Contraseña de MySQL
        database="login_db"    # Base de datos creada
    )

import re
from datetime import datetime

def normalizar_texto(s: str) -> str:
    return re.sub(r'\s+', ' ', str(s).strip().lower())

def guardar_productos_en_bd(proveedor: str, productos: list[dict]):
    """
    Sobrescribe todos los productos de 'proveedor' con los nuevos.
    productos: [{'product': str, 'price': float, ...}, ...]
    """
    conn = get_connection()
    try:
        conn.start_transaction()
        cur = conn.cursor()

        # 1) borrar lo anterior de ese proveedor
        cur.execute("DELETE FROM productos WHERE proveedor=%s", (proveedor,))

        # 2) insertar los nuevos
        filas = []
        for p in productos:
            prod = str(p['product']).strip()
            precio = float(p['price'])
            filas.append((
                proveedor,
                prod,
                normalizar_texto(prod),
                precio
            ))

        if filas:
            cur.executemany(
                """INSERT INTO productos (proveedor, producto, producto_normalizado, precio, actualizado_a)
                   VALUES (%s, %s, %s, %s, NOW())""",
                filas
            )

        conn.commit()
        print(f"[DB] {len(filas)} productos guardados para {proveedor}")
    except Exception as e:
        conn.rollback()
        print("[DB ERROR] guardar_productos_en_bd:", e)
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
    # no interceptes estáticos ni el propio /login
    if request.path.startswith("/static/") or request.path == "/login":
        return e, 404
    return redirect(url_for("login"))


@app.route("/logout", methods=["POST"])
def logout():
    session.pop("user", None)              # borra la sesión
    flash("Sesión cerrada", "info")        # opcional: mensaje
    return redirect(url_for("login"))      # redirige al login


@app.before_request
def _redirect_root_to_login():
    if request.path == "/":
        return redirect(url_for("login"))
 

@app.route('/business/setup', methods=['GET', 'POST'])
@login_required
def business_setup():
    """Modal/página para configurar datos del negocio antes de generar PDFs"""
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

# === MEJORA EN LA GENERACIÓN DE PDFS ===
def generate_pdf_for_supplier(supplier_name, items, business_data, filename):
    """Genera un PDF de pedido por proveedor - MEJORADO"""
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
    
    # Título principal
    story.append(Paragraph(f"PEDIDO PARA {supplier_name.upper()}", title_style))
    story.append(Spacer(1, 20))

    # Información del comercio
    story.append(Paragraph("DATOS DEL COMERCIO", header_style))
    business_info_text = f"""
    <b>Comercio:</b> {business_data.get('business_name', 'N/A')}<br/>
    <b>Persona de Contacto:</b> {business_data.get('contact_person', 'N/A')}<br/>
    <b>Dirección de Entrega:</b> {business_data.get('address', 'N/A')}<br/>
    <b>Teléfono:</b> {business_data.get('phone', 'N/A')}<br/>
    <b>Email:</b> {business_data.get('email', 'N/A')}<br/>
    """
    story.append(Paragraph(business_info_text, styles['Normal']))
    story.append(Spacer(1, 25))

    # Fecha del pedido
    story.append(Paragraph(f"<b>Fecha del Pedido:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}", styles['Normal']))
    story.append(Paragraph(f"<b>Número de Pedido:</b> {datetime.now().strftime('%Y%m%d%H%M%S')}", styles['Normal']))
    story.append(Spacer(1, 25))

    # Tabla de productos
    story.append(Paragraph("DETALLE DEL PEDIDO", header_style))
    
    table_data = [['Producto', 'Cantidad', 'Precio Unitario', 'Subtotal']]
    total = 0
    
    for item in items:
        quantity = item['quantity']
        price = item['price']
        subtotal = quantity * price
        total += subtotal
        table_data.append([
            Paragraph(item['product'], styles['Normal']),
            str(quantity),
            f"${price:,.2f}",
            f"${subtotal:,.2f}"
        ])

    # Fila de total
    table_data.append(['', '', Paragraph('<b>TOTAL:</b>', styles['Normal']), Paragraph(f'<b>${total:,.2f}</b>', styles['Normal'])])

    table = Table(table_data, colWidths=[3.5*inch, 0.8*inch, 1.2*inch, 1.2*inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.navy),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (0, 1), (0, -2), 'LEFT'),  # Align product names to left
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 11),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('TOPPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -2), colors.beige),
        ('BACKGROUND', (0, -1), (-1, -1), colors.lightblue),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))

    story.append(table)
    story.append(Spacer(1, 30))

    # Información del proveedor
    story.append(Paragraph("DATOS DEL PROVEEDOR", header_style))
    supplier_location = DEFAULT_LOCATIONS.get(supplier_name, 'Ubicación no especificada')
    supplier_phone = SUPPLIER_PHONES.get(supplier_name, 'Teléfono no disponible')
    
    supplier_info_text = f"""
    <b>Proveedor:</b> {supplier_name}<br/>
    <b>Dirección:</b> {supplier_location}<br/>
    <b>Teléfono:</b> {supplier_phone}<br/>
    """
    story.append(Paragraph(supplier_info_text, styles['Normal']))
    story.append(Spacer(1, 20))

    # Notas adicionales
    story.append(Paragraph("NOTAS", header_style))
    notes_text = """
    • Por favor confirme disponibilidad de todos los productos<br/>
    • Solicite tiempo estimado de entrega<br/>
    • Verifique condiciones de pago<br/>
    • Este pedido está sujeto a confirmación
    """
    story.append(Paragraph(notes_text, styles['Normal']))

    doc.build(story)
    return pdf_path

# === MEJORA EN EL MENSAJE DE WHATSAPP ===
def create_whatsapp_message(supplier_name, items, business_data, total):
    """Arma el texto para WhatsApp del pedido - MEJORADO"""
    contact_person = business_data.get('contact_person', business_data.get('business_name', 'Cliente'))
    
    message = f"""🛒 *NUEVO PEDIDO*

👤 *De:* {contact_person}
🏢 *Comercio:* {business_data.get('business_name', 'N/A')}

📍 *Datos de Entrega:*
• Dirección: {business_data.get('address', 'N/A')}
• Teléfono: {business_data.get('phone', 'N/A')}
• Email: {business_data.get('email', 'N/A')}

📦 *Productos Solicitados:*
"""
    
    for i, item in enumerate(items, 1):
        subtotal = item['quantity'] * item['price']
        message += f"{i}. *{item['product']}*\n"
        message += f"   📦 Cantidad: {item['quantity']}\n"
        message += f"   💰 Precio: ${item['price']:,.2f} c/u\n"
        message += f"   💵 Subtotal: ${subtotal:,.2f}\n\n"

    message += f"💰 *TOTAL DEL PEDIDO: ${total:,.2f}*\n\n"
    message += f"📅 *Fecha:* {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
    message += f"🔢 *Nº Pedido:* {datetime.now().strftime('%Y%m%d%H%M%S')}\n\n"
    
    message += "❓ *Por favor confirme:*\n"
    message += "• ✅ Disponibilidad de productos\n"
    message += "• 🚚 Tiempo de entrega\n"
    message += "• 💳 Condiciones de pago\n"
    message += "• 📋 Cualquier modificación necesaria\n\n"
    
    message += "¡Gracias por su atención! 🙏"
    
    return message

# === ENDPOINT MEJORADO PARA GENERAR PDFS ===
@app.route('/cart/generate_pdfs', methods=['POST'])
@login_required
def generate_pdfs():
    user = session['user']
    
    # Verificar que hay datos del negocio
    if user not in business_data:
        return jsonify({
            'error': 'Datos del negocio requeridos',
            'show_business_form': True,
            'message': 'Primero complete la información de su comercio'
        }), 400
    
    # Validar que los datos obligatorios estén completos
    biz = business_data[user]
    required_fields = ['business_name', 'address', 'phone']
    missing_fields = [field for field in required_fields if not biz.get(field, '').strip()]
    
    if missing_fields:
        return jsonify({
            'error': 'Datos incompletos',
            'missing_fields': missing_fields,
            'show_business_form': True,
            'message': f'Complete los siguientes campos: {", ".join(missing_fields)}'
        }), 400
    
    if user not in user_carts or not user_carts[user]:
        return jsonify({'error': 'Carrito vacío'}), 400

    cart = user_carts[user]

    # Agrupar por proveedor
    suppliers = {}
    for item in cart:
        suppliers.setdefault(item.get('supplier', 'Proveedor Desconocido'), []).append(item)

    pdfs_generated = []
    whatsapp_links = []
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    try:
        for supplier_name, items in suppliers.items():
            # Generar nombre de archivo único
            safe_supplier = supplier_name.replace(' ', '_').replace('/', '_')
            safe_user = user.replace(' ', '_').replace('/', '_')
            pdf_filename = f"pedido_{safe_supplier}_{safe_user}_{timestamp}.pdf"
            
            # Generar PDF
            pdf_path = generate_pdf_for_supplier(supplier_name, items, biz, pdf_filename)
            
            # Calcular total
            total = sum(i['quantity'] * i['price'] for i in items)
            
            # Generar mensaje y link de WhatsApp
            wa_msg = create_whatsapp_message(supplier_name, items, biz, total)
            phone = SUPPLIER_PHONES.get(supplier_name, '+541156649404')  # fallback
            
            # Crear URL de WhatsApp con mensaje
            wa_url = f"https://wa.me/{phone.replace('+', '')}?text={urllib.parse.quote(wa_msg)}"

            pdfs_generated.append({
                'supplier': supplier_name,
                'filename': pdf_filename,
                'total': total,
                'total_formatted': f"${total:,.2f}",
                'items_count': len(items),
                'pdf_path': pdf_path,
                'phone': phone
            })
            
            whatsapp_links.append({
                'supplier': supplier_name,
                'phone': phone,
                'url': wa_url,
                'total': total,
                'total_formatted': f"${total:,.2f}",
                'message_preview': wa_msg[:100] + '...' if len(wa_msg) > 100 else wa_msg
            })

        return jsonify({
            'success': True,
            'pdfs': pdfs_generated,
            'whatsapp_links': whatsapp_links,
            'total_suppliers': len(suppliers),
            'message': f'Se generaron {len(pdfs_generated)} PDFs exitosamente',
            'business_info': biz  # Para confirmar en el frontend
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Error generando PDFs: {str(e)}',
            'message': 'Ocurrió un error al generar los documentos'
        }), 500

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
        errors.append('Dirección es obligatoria')
    if not data.get('phone', '').strip():
        errors.append('Teléfono es obligatorio')
    
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