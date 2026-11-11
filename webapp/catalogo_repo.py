# ============================================================================
# PROPIEDAD INTELECTUAL Y LICENCIA COMERCIAL CERRADA
# ============================================================================
# Autor Legal y Titular de Derechos: JAVIER ILLAN GONZALEZ
# Organización: ORANGE CREW
# Contacto: ILLANJAVIER9@GMAIL.COM
#
# ADVERTENCIA LEGAL (MÉXICO Y GLOBAL):
# Este código fuente y su arquitectura son propiedad intelectual exclusiva de
# JAVIER ILLAN GONZALEZ. Queda estrictamente prohibida su reproducción,
# distribución, modificación, ingeniería inversa, copia o uso comercial sin la
# autorización expresa y por escrito del autor. Obra protegida conforme a la
# Ley Federal del Derecho de Autor y tratados internacionales aplicables.
# ============================================================================
import os
import sys
import pandas as pd

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../backend/app/exporters')))

from db import get_connection


def importar_desde_excel(empresa_id, ruta_archivo, nombre_hoja=None):
    if nombre_hoja:
        df = pd.read_excel(ruta_archivo, sheet_name=nombre_hoja)
    else:
        df = pd.read_excel(ruta_archivo)

    columnas = [str(c).strip().lower() for c in df.columns]
    if "cuenta" in columnas and "descripcion" in columnas:
        idx_cuenta = columnas.index("cuenta")
        idx_desc = columnas.index("descripcion")
    elif len(df.columns) >= 2:
        idx_cuenta, idx_desc = 0, 1
    else:
        raise ValueError("El archivo debe tener al menos dos columnas: cuenta y descripción.")

    filas = []
    for _, fila in df.iterrows():
        cuenta = fila.iloc[idx_cuenta]
        descripcion = fila.iloc[idx_desc]
        if pd.isna(cuenta) or pd.isna(descripcion):
            continue
        filas.append((str(cuenta).strip(), str(descripcion).strip()))

    return _guardar(empresa_id, filas)


def importar_desde_texto(empresa_id, texto):
    filas = []
    for linea in texto.strip().splitlines():
        partes = linea.split("\t")
        if len(partes) < 2:
            partes = linea.split(",")
        if len(partes) >= 2 and partes[0].strip():
            filas.append((partes[0].strip(), partes[1].strip()))
    return _guardar(empresa_id, filas)


def _inferir_jerarquia(filas):
    """
    filas es lista de (cuenta, descripcion).
    Retorna lista de (cuenta, descripcion, cuenta_padre, nivel)
    """
    # Ordenar por cuenta
    filas = sorted(filas, key=lambda x: str(x[0]))
    resultado = []
    
    # Mantener rastro del ultimo padre potencial por nivel
    padres_por_nivel = {}
    
    for cuenta, desc in filas:
        cuenta_str = str(cuenta).strip()
        nivel = 1
        padre = None
        
        if '-' in cuenta_str:
            # Formato 100-01-001
            partes = cuenta_str.split('-')
            
            # Buscar el nivel real omitiendo ceros finales.
            # Ej: 100-00-000 es nivel 1. 100-01-000 es nivel 2. 100-01-001 es nivel 3.
            nivel_real = 0
            for p in partes:
                if p.strip().strip('0') != '':
                    nivel_real += 1
            
            nivel = max(1, nivel_real)
            
            if nivel > 1:
                # El padre es el nivel anterior que guardamos
                if (nivel - 1) in padres_por_nivel:
                    padre = padres_por_nivel[nivel - 1]
            
            padres_por_nivel[nivel] = cuenta_str
            # Limpiar niveles mas profundos
            for n in list(padres_por_nivel.keys()):
                if n > nivel:
                    del padres_por_nivel[n]
                    
        else:
            # Formato sin guiones (1000, 1100, 1110)
            # 1000 -> nivel 1. 1100 -> nivel 2. 1110 -> nivel 3.
            ceros_finales = len(cuenta_str) - len(cuenta_str.rstrip('0'))
            longitud = len(cuenta_str)
            
            if longitud == 4:
                if ceros_finales == 3: nivel = 1
                elif ceros_finales == 2: nivel = 2
                elif ceros_finales == 1: nivel = 3
                else: nivel = 4
            elif longitud > 4:
                # Aproximacion generica
                nivel = 1 if ceros_finales >= (longitud - 1) else (2 if ceros_finales >= (longitud - 2) else 3)
            
            if nivel > 1:
                if (nivel - 1) in padres_por_nivel:
                    padre = padres_por_nivel[nivel - 1]
            
            padres_por_nivel[nivel] = cuenta_str
            for n in list(padres_por_nivel.keys()):
                if n > nivel:
                    del padres_por_nivel[n]

        resultado.append((cuenta_str, desc, padre, nivel))
        
    return resultado


def _guardar(empresa_id, filas):
    filas_con_jerarquia = _inferir_jerarquia(filas)
    con = get_connection()
    cur = con.cursor()
    for cuenta, descripcion, padre, nivel in filas_con_jerarquia:
        cur.execute(
            """INSERT INTO cuentas_catalogo (empresa_id, cuenta, descripcion, cuenta_padre, nivel)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(empresa_id, cuenta) DO UPDATE SET 
                  descripcion = excluded.descripcion,
                  cuenta_padre = excluded.cuenta_padre,
                  nivel = excluded.nivel
            """,
            (empresa_id, cuenta, descripcion, padre, nivel),
        )
    con.commit()
    con.close()
    return len(filas)


def listar(empresa_id, buscar=None):
    con = get_connection()
    if buscar:
        patron = f"%{buscar}%"
        filas = con.execute(
            """SELECT * FROM cuentas_catalogo WHERE empresa_id = ?
               AND (cuenta ILIKE ? OR descripcion ILIKE ?) ORDER BY cuenta""",
            (empresa_id, patron, patron),
        ).fetchall()
    else:
        filas = con.execute(
            "SELECT * FROM cuentas_catalogo WHERE empresa_id = ? ORDER BY cuenta", (empresa_id,)
        ).fetchall()
    con.close()
    return filas


def eliminar_todo(empresa_id):
    con = get_connection()
    con.execute("DELETE FROM cuentas_catalogo WHERE empresa_id = ?", (empresa_id,))
    con.commit()
    con.close()


class TotalCuentasSincronizadas(int):
    def __new__(cls, total, nuevas=0, reemplazadas=0, iguales=0, modo="incremental"):
        inst = super().__new__(cls, int(total))
        inst.nuevas = int(nuevas)
        inst.reemplazadas = int(reemplazadas)
        inst.iguales = int(iguales)
        inst.modo = str(modo or "incremental")
        return inst


def _construir_id_comparacion(id_contpaqi: int, cuenta: str, descripcion: str) -> str:
    return f"{int(id_contpaqi or 0)}|{str(cuenta or '').strip()}|{str(descripcion or '').strip()}"


def sincronizar_desde_contpaqi(empresa_id, nombre_interno_empresa, forzar=False, callback_etapa=None):
    """`nombre_interno_empresa` es empresas.base_datos_contpaqi (nombre
    interno de base de datos de CONTPAQi, ej. "ctNOMBRE_EMPRESA"), no la
    ruta de un archivo -- el parámetro se llamaba `ruta_contpaqi` antes,
    lo cual era engañoso: nunca fue una ruta de disco."""
    from contpaqi_bridge_client import ErrorBridgeContpaqi, listar_cuentas
    from db import get_connection
    try:
        total_previo = 0
        try:
            with get_connection() as con_prev:
                total_previo = con_prev.execute(
                    "SELECT COUNT(*) FROM cuentas_catalogo WHERE empresa_id = ?", (empresa_id,)
                ).fetchone()[0] or 0
        except Exception:
            total_previo = 0

        if callback_etapa:
            callback_etapa("leyendo_sdk", 0, total_previo)

        data = listar_cuentas({
            "empresa": nombre_interno_empresa,
            "forzar": bool(forzar),
            "totalEstimado": int(total_previo),
        })
        if not data.get("exito"):
            raise Exception(data.get("mensaje", "Error desconocido del puente C#"))
        
        cuentas = data.get("cuentas", [])
        nuevas_sdk = int(data.get("nuevas") or 0)
        modo_sdk = str(data.get("modo") or ("completo" if forzar else "incremental"))
        if not cuentas:
            return TotalCuentasSincronizadas(0, nuevas=0, reemplazadas=0, iguales=0, modo=modo_sdk)

        limpias = {}
        agrupadores = {}
        ids_contpaqi = {}
        ids_comparacion = {}

        for c in cuentas:
            _limp = lambda v: str(v).replace("\x00", "").strip() if v else ""
            _cod = _limp(c.get("codigo"))
            _nom = _limp(c.get("nombre"))
            _agr = _limp(c.get("agrupadorSat") or c.get("agrupador_sat"))
            _id_ct = int(c.get("idContpaqi") or c.get("id_contpaqi") or 0)
            _id_comp = _limp(c.get("idComparacion") or c.get("id_comparacion")) or _construir_id_comparacion(_id_ct, _cod, _nom)
            if _cod and _cod != "_CUADRE" and not _cod.startswith("_"):
                limpias[_cod] = _nom
                agrupadores[_cod] = _agr
                ids_contpaqi[_cod] = _id_ct
                ids_comparacion[_cod] = _id_comp

        filas_jerarquia = _inferir_jerarquia(list(limpias.items()))

        con = get_connection()
        cur = con.cursor()
        # Asegurar que las columnas existen en instalaciones previas
        cur.execute("ALTER TABLE cuentas_catalogo ADD COLUMN IF NOT EXISTS id_contpaqi INTEGER")
        cur.execute("ALTER TABLE cuentas_catalogo ADD COLUMN IF NOT EXISTS id_comparacion TEXT")

        existentes_rows = cur.execute(
            """SELECT id, cuenta, descripcion, cuenta_padre, nivel, codigo_agrupador, id_contpaqi, id_comparacion
               FROM cuentas_catalogo WHERE empresa_id = ?""",
            (empresa_id,),
        ).fetchall()

        por_cuenta = {}
        por_id_contpaqi = {}
        for r in existentes_rows:
            cta_str = str(r["cuenta"] or "").strip()
            por_cuenta[cta_str] = r
            id_ct_db = int(r["id_contpaqi"] or 0)
            if id_ct_db > 0:
                por_id_contpaqi[id_ct_db] = r

        a_insertar_o_reemplazar = []
        a_reemplazar_por_id_pk = []
        nuevas_cnt = 0
        reemplazadas_cnt = 0
        iguales_cnt = 0

        for cod, nom, padre, nivel in filas_jerarquia:
            padre_str = padre or ""
            agr_str = agrupadores.get(cod, "")
            id_ct = ids_contpaqi.get(cod, 0)
            id_comp = ids_comparacion.get(cod) or _construir_id_comparacion(id_ct, cod, nom)

            # 1) ¿Existía con el mismo id_contpaqi pero cambió su código de cuenta?
            fila_mismo_id = por_id_contpaqi.get(id_ct) if id_ct > 0 else None
            if fila_mismo_id is not None and str(fila_mismo_id["cuenta"]).strip() != cod:
                a_reemplazar_por_id_pk.append((
                    cod, nom, padre_str, nivel, agr_str, id_ct, id_comp, fila_mismo_id["id"]
                ))
                reemplazadas_cnt += 1
                continue

            # 2) ¿Existe ya por código de cuenta?
            fila_prev = por_cuenta.get(cod)
            if fila_prev is None:
                a_insertar_o_reemplazar.append((
                    empresa_id, cod, nom, padre_str, nivel, agr_str, id_ct, id_comp
                ))
                nuevas_cnt += 1
            else:
                id_comp_prev = str(fila_prev["id_comparacion"] or "").strip()
                desc_prev = str(fila_prev["descripcion"] or "").strip()
                padre_prev = str(fila_prev["cuenta_padre"] or "").strip()
                nivel_prev = int(fila_prev["nivel"] or 1)
                id_ct_prev = int(fila_prev["id_contpaqi"] or 0)

                # Si la huella id_comparacion cambió (o cambió descripción / id_contpaqi)
                cambio_negocio = (desc_prev != nom) or (id_ct > 0 and id_ct_prev > 0 and id_ct != id_ct_prev)
                cambio_estructura = (id_comp_prev != id_comp) or (padre_prev != padre_str) or (nivel_prev != nivel)

                if cambio_negocio or cambio_estructura:
                    a_insertar_o_reemplazar.append((
                        empresa_id, cod, nom, padre_str, nivel, agr_str, id_ct, id_comp
                    ))
                    if cambio_negocio:
                        reemplazadas_cnt += 1
                    else:
                        iguales_cnt += 1
                else:
                    iguales_cnt += 1

        if callback_etapa and (a_insertar_o_reemplazar or a_reemplazar_por_id_pk):
            callback_etapa("guardando_bd", len(a_insertar_o_reemplazar) + len(a_reemplazar_por_id_pk), len(filas_jerarquia))

        # Si el usuario pidió forzar relectura completa, también eliminamos cuentas que ya no existen en CONTPAQi
        if forzar and existentes_rows:
            codigos_actuales = set(limpias.keys())
            ids_a_borrar = [
                r["id"] for r in existentes_rows
                if str(r["cuenta"] or "").strip() not in codigos_actuales
            ]
            if ids_a_borrar:
                placeholders = ",".join("?" for _ in ids_a_borrar)
                cur.execute(f"DELETE FROM cuentas_catalogo WHERE id IN ({placeholders})", tuple(ids_a_borrar))

        if a_reemplazar_por_id_pk:
            cur.executemany(
                """UPDATE cuentas_catalogo
                   SET cuenta = ?, descripcion = ?, cuenta_padre = ?, nivel = ?,
                       codigo_agrupador = ?, id_contpaqi = ?, id_comparacion = ?
                   WHERE id = ?""",
                a_reemplazar_por_id_pk,
            )

        if a_insertar_o_reemplazar:
            cur.executemany(
                """INSERT INTO cuentas_catalogo
                      (empresa_id, cuenta, descripcion, cuenta_padre, nivel, codigo_agrupador, id_contpaqi, id_comparacion)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(empresa_id, cuenta) DO UPDATE SET
                      descripcion = excluded.descripcion,
                      cuenta_padre = excluded.cuenta_padre,
                      nivel = excluded.nivel,
                      codigo_agrupador = excluded.codigo_agrupador,
                      id_contpaqi = excluded.id_contpaqi,
                      id_comparacion = excluded.id_comparacion""",
                a_insertar_o_reemplazar,
            )

        con.commit()
        con.close()
        nuevas_final = max(nuevas_cnt, nuevas_sdk if reemplazadas_cnt == 0 else nuevas_cnt)
        return TotalCuentasSincronizadas(
            len(filas_jerarquia),
            nuevas=nuevas_final,
            reemplazadas=reemplazadas_cnt,
            iguales=iguales_cnt,
            modo=modo_sdk,
        )
    except Exception as e:
        raise Exception(f"No se pudo sincronizar con CONTPAQi: {str(e)}")
