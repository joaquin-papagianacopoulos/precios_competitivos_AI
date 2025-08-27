from flask import Flask, render_template, request, jsonify, send_from_directory, session, flash, redirect, url_for
import pandas as pd
import os
from werkzeug.utils import secure_filename
import json
from datetime import datetime
import re
import mysql.connector

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
    if 'file' not in request.files:
        return jsonify({'error': 'No se seleccionó archivo'})
    
    file = request.files['file']
    supplier_name = request.form.get('supplier_name', 'Proveedor Sin Nombre')
    
    if file.filename == '':
        return jsonify({'error': 'No se seleccionó archivo'})
    
    if file and file.filename.lower().endswith(('.xlsx', '.xls')):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        try:
            file.save(filepath)
            
            # Procesar el archivo
            products, debug_info = processor.process_excel_file(filepath, supplier_name)
            
            if products:
                # Guardar en memoria
                price_lists[supplier_name] = {
                    'filename': filename,
                    'upload_date': datetime.now().isoformat(),
                    'products': products,
                    'total_products': len(products),
                    'debug_info': debug_info
                }
                
                return jsonify({
                    'success': True,
                    'message': f'Archivo procesado exitosamente. {len(products)} productos cargados.',
                    'supplier': supplier_name,
                    'total_products': len(products),
                    'debug_info': debug_info[:5]  # Solo mostrar los primeros 5 items de debug
                })
            else:
                return jsonify({
                    'error': 'No se pudieron extraer productos del archivo',
                    'debug_info': debug_info
                })
                
        except Exception as e:
            return jsonify({
                'error': f'Error procesando archivo: {str(e)}',
                'debug_info': [f'Error general: {str(e)}']
            })
        finally:
            # Limpiar archivo temporal
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except:
                pass
    else:
        return jsonify({'error': 'Tipo de archivo no soportado. Use .xlsx o .xls'})

@app.route('/search')
def search_products():
    query = request.args.get('q', '').lower().strip()
    
    if not query:
        return jsonify({'results': [], 'total': 0})
    
    if not price_lists:
        return jsonify({'results': [], 'total': 0, 'message': 'No hay listas de precios cargadas'})
    
    # Buscar en todas las listas
    all_results = []
    
    for supplier, data in price_lists.items():
        for product in data['products']:
            if query in product['product'].lower():
                all_results.append(product)
    
    # Ordenar por precio (menor a mayor)
    all_results.sort(key=lambda x: x['price'])
    
    # Agregar información adicional
    for result in all_results:
        result['price_formatted'] = f"${result['price']:,.2f}"
    
    return jsonify({
        'results': all_results,
        'total': len(all_results),
        'query': query,
        'suppliers_count': len(price_lists)
    })

@app.route('/lists')
def get_loaded_lists():
    """Obtener información de las listas cargadas"""
    lists_info = []
    for supplier, data in price_lists.items():
        lists_info.append({
            'supplier': supplier,
            'filename': data['filename'],
            'upload_date': data['upload_date'],
            'total_products': data['total_products']
        })
    
    return jsonify({
        'lists': lists_info,
        'total': len(lists_info)
    })

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

        user = get_user(username, password)
        if user:
            session["user"] = username
            return redirect(url_for("index_pagina"))   # si login OK → index
        else:
            flash("Usuario o contraseña incorrectos", "error")
            return redirect(url_for("login"))

    # Si es GET → mostrar login
    return render_template("login.html")
 


@app.route("/index")
def index_pagina():
    if "user" not in session:
        return redirect(url_for("login"))   # si no hay sesión → login
    return render_template("index.html", user=session["user"])


@app.route("/logout", methods=["POST"])
def logout():
    session.pop("user", None)
    flash("Sesión cerrada", "info")
    return redirect(url_for("login"))



if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)