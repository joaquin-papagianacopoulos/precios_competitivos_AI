#!/usr/bin/env python3
"""
Normalizador Masivo de Listas de Precios V2
Optimizado para procesar múltiples archivos Excel
Comparación de productos entre proveedores con matching difuso y PDF
"""

import pandas as pd
import re
import os
import sys
import json
import unicodedata
import logging
from dataclasses import dataclass
from typing import List, Dict, Optional
import argparse
from difflib import SequenceMatcher
from reportlab.lib.pagesizes import A3
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
import time

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class ProductRecord:
    """Estructura optimizada para almacenar productos"""
    codigo: str
    descripcion: str
    precio: float
    proveedor: str
    categoria: str = ""
    marca: str = ""
    raw_line: str = ""

class OptimizedPriceNormalizer:
    def __init__(self):
        self.products = []

    def normalize_text(self, text: str) -> str:
        normalized = text.lower().strip()
        normalized = ''.join(c for c in unicodedata.normalize('NFD', normalized) 
                             if unicodedata.category(c) != 'Mn')
        normalized = re.sub(r'[^\w\s.-]', ' ', normalized)
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        return normalized

    def extract_price(self, text: str) -> Optional[float]:
        if not text:
            return None
        text_str = str(text).strip()
        try:
            clean_text = re.sub(r'[^\d.,]', '', text_str)
            if clean_text:
                if ',' in clean_text and '.' in clean_text:
                    last_comma = clean_text.rfind(',')
                    last_dot = clean_text.rfind('.')
                    if last_dot > last_comma:
                        clean_text = clean_text.replace(',', '')
                    else:
                        clean_text = clean_text.replace('.', '').replace(',', '.')
                elif ',' in clean_text:
                    if clean_text.count(',') == 1 and len(clean_text.split(',')[1]) <= 2:
                        clean_text = clean_text.replace(',', '.')
                    else:
                        clean_text = clean_text.replace(',', '')
                price = float(clean_text)
                if 0.01 <= price <= 10000000:
                    return price
        except ValueError:
            pass
        return None

    def extract_code(self, text: str) -> str:
        match = re.search(r'\b[A-Z0-9]{3,15}\b', text)
        return match.group(0).upper() if match else ""

    def classify_category(self, description: str) -> str:
        keywords = {
            'electronica': ['notebook', 'laptop', 'monitor', 'teclado', 'mouse', 'auricular'],
            'hogar': ['mesa', 'silla', 'lampara', 'decoracion'],
            'oficina': ['papel', 'tinta', 'carpeta', 'archivo'],
            'herramientas': ['taladro', 'destornillador', 'martillo'],
        }
        desc_lower = description.lower()
        for category, words in keywords.items():
            if any(word in desc_lower for word in words):
                return category
        return "general"

    def process_excel_optimized(self, excel_path: str) -> List[ProductRecord]:
        logger.info(f"Procesando Excel: {excel_path}")
        products = []
        supplier_name = os.path.splitext(os.path.basename(excel_path))[0]

        try:
            df = pd.read_excel(excel_path, sheet_name=0).fillna('')

            # Detectar columna de descripción y precio automáticamente
            cols = [self.normalize_text(str(c)) for c in df.columns]
            col_desc = None
            col_price = None
            for i, c in enumerate(cols):
                if 'desc' in c or 'nombre' in c or 'descripcion' in c or 'articulo' in c:
                    col_desc = df.columns[i]
                if 'precio' in c or 'pcio' in c or 'pvp' in c:
                    col_price = df.columns[i]

            if col_desc is None:
                col_desc = df.columns[0]
            if col_price is None:
                col_price = df.columns[-1]

            for _, row in df.iterrows():
                descripcion_raw = str(row[col_desc])
                descripcion = self.normalize_text(descripcion_raw)

                precio_raw = str(row[col_price])
                # Limpiar precio: 15.716,232 -> 15716.232
                precio_clean = re.sub(r'\.', '', precio_raw)
                precio_clean = precio_clean.replace(',', '.')
                try:
                    precio = float(precio_clean)
                except ValueError:
                    continue  # ignorar fila si no se puede convertir

                if descripcion and precio:
                    products.append(ProductRecord(
                        codigo=self.extract_code(descripcion_raw),
                        descripcion=descripcion,
                        precio=precio,
                        proveedor=supplier_name,
                        categoria=self.classify_category(descripcion)
                    ))
        except Exception as e:
            logger.error(f"Error procesando {excel_path}: {e}")

        logger.info(f"Extraídos {len(products)} productos de {supplier_name}")
        return products



# --- Matching difuso y comparación de precios ---
def compare_products(products: List[ProductRecord], similarity_threshold=0.8):
    grouped = []
    used_indices = set()
    for i, p1 in enumerate(products):
        if i in used_indices:
            continue
        group = [p1]
        used_indices.add(i)
        for j, p2 in enumerate(products):
            if j in used_indices:
                continue
            ratio = SequenceMatcher(None, p1.descripcion, p2.descripcion).ratio()
            if ratio >= similarity_threshold:
                group.append(p2)
                used_indices.add(j)
        grouped.append(group)

    best_products = []
    stats = {"duplicados": 0, "ganados_por": {}}

    for group in grouped:
        best = min(group, key=lambda x: x.precio)
        worst_candidates = [p for p in group if p != best]
        
        proveedor_perdedor = worst_candidates[0].proveedor if worst_candidates else ""
        precio_perdedor = worst_candidates[0].precio if worst_candidates else 0.0
        
        if len(group) > 1:
            stats["duplicados"] += 1
            stats["ganados_por"][best.proveedor] = stats["ganados_por"].get(best.proveedor, 0) + 1
        
        best_products.append({
            "descripcion": best.descripcion,
            "precio": best.precio,
            "proveedor_ganador": best.proveedor,
            "proveedor_perdedor": proveedor_perdedor,
            "precio_perdedor": precio_perdedor
        })

    return best_products, stats

# --- Exportar PDF ---
def export_to_pdf(best_products: List[Dict], stats: Dict, pdf_path: str = "comparacion.pdf"):
    doc = SimpleDocTemplate(pdf_path, pagesize=A3)
    elements = []
    styles = getSampleStyleSheet()
    elements.append(Paragraph("Comparación de Productos - Mejor Precio por Proveedor", styles['Heading1']))
    elements.append(Paragraph(f"Total duplicados encontrados: {stats['duplicados']}", styles['Normal']))
    
    data = [["Descripción", "Proveedor Ganador", "Precio Ganador", "Proveedor Perdedor", "Precio Perdedor"]]
    for p in best_products:
        data.append([
            p['descripcion'],
            p['proveedor_ganador'],
            f"${p['precio']:.2f}",
            p['proveedor_perdedor'],
            f"${p['precio_perdedor']:.2f}"
        ])
    
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.lightblue),
        ('TEXTCOLOR',(0,0),(-1,0),colors.black),
        ('ALIGN',(2,1),(-1,-1),'RIGHT'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold')
    ]))
    elements.append(table)
    doc.build(elements)
    logger.info(f"PDF generado: {pdf_path}")

# --- MAIN ---
def main():
    parser = argparse.ArgumentParser(description='Normalizador Masivo de Listas de Precios')
    parser.add_argument('files', nargs='+', help='Archivos Excel a procesar')
    parser.add_argument('-o', '--output', default='normalized_data.json', help='Archivo de salida JSON')
    parser.add_argument('-p', '--pdf', default='comparacion.pdf', help='Archivo de salida PDF')
    args = parser.parse_args()

    valid_files = [f for f in args.files if os.path.exists(f)]
    if not valid_files:
        logger.error("No se encontraron archivos válidos")
        sys.exit(1)

    start_time = time.time()
    normalizer = OptimizedPriceNormalizer()
    
    # Procesar cada archivo Excel
    all_products = []
    for file_path in valid_files:
        if file_path.lower().endswith(('.xlsx', '.xls')):
            products = normalizer.process_excel_optimized(file_path)
            all_products.extend(products)
    normalizer.products = all_products

    # Eliminar duplicados exactos y guardar JSON
    normalizer.save_normalized_data = lambda f=args.output: json.dump(
        [p.__dict__ for p in normalizer.products], open(f, 'w', encoding='utf-8'), ensure_ascii=False, indent=2
    )
    normalizer.save_normalized_data()
    best_products, stats = compare_products(normalizer.products)
    best_products_json = [
    {
        "descripcion": p["descripcion"],
        "precio": p["precio"],
        "proveedor": p["proveedor_ganador"]
    }
    for p in best_products
    ]

    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(best_products_json, f, ensure_ascii=False, indent=2)

    export_to_pdf(best_products, stats, args.pdf)
    # Mostrar resumen simple
    print(f"Productos totales procesados: {len(normalizer.products)}")

    # Comparación difusa y exportación a PDF
    best_products, stats = compare_products(normalizer.products)
    export_to_pdf(best_products, stats, args.pdf)

    elapsed_time = time.time() - start_time
    logger.info(f"Procesamiento completado en {elapsed_time:.2f} segundos")

if __name__ == "__main__":
    main()
