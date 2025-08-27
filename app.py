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

# Crear carpeta de uploads si no existe
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Almacenamiento global para las listas de precios
price_lists = {}

# Ubicaciones por defecto de mayoristas
DEFAULT_LOCATIONS = {
    'Mayorista Central': 'Av. Corrientes 1234, Buenos Aires, Argentina',
    'Distribuidora Norte': 'San Martín 567, Córdoba, Argentina',
    'Comercial Sur': 'Pellegrini 890, Rosario, Argentina',
    'Proveedor Express': 'Florida 456, Buenos Aires, Argentina'
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
                                        'location': DEFAULT_LOCATIONS.get(supplier_name, 'Buenos Aires, Argentina')
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
        'product': r['producto'],
        'price': float(r['precio']),
        'price_formatted': f"${float(r['precio']):,.2f}",
        'supplier': r['proveedor'],
        'location': DEFAULT_LOCATIONS.get(r['proveedor'], 'Argentina'),
        'updated_at': (r['actualizado_a'].isoformat() if r.get('actualizado_a') else None)
    } for r in filas]
    return jsonify({'results': results, 'total': len(results), 'query': q,
                    'suppliers_count': len(set(r['proveedor'] for r in filas))})

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
        password="12345678",# Contraseña de MySQL
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
 



if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)