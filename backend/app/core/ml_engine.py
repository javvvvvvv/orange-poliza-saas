# -*- coding: utf-8 -*-
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
"""
Motor Predictivo de Inteligencia Artificial (Fase 5)
Entrena un modelo local de scikit-learn con los datos historicos de 
asignacion manual de cada empresa (TF-IDF + SGDClassifier).
"""
import os
try:
    import joblib
    import pandas as pd
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import SGDClassifier
    from sklearn.pipeline import Pipeline
    _ML_DISPONIBLE = True
except ImportError:
    joblib = None
    pd = None
    TfidfVectorizer = None
    SGDClassifier = None
    Pipeline = None
    _ML_DISPONIBLE = False
try:
    from .fuzzy_matcher import _tokens
except ImportError:
    from fuzzy_matcher import _tokens

_MODELS_DIR = os.path.join(os.path.dirname(__file__), "ml_models")
os.makedirs(_MODELS_DIR, exist_ok=True)

def limpiar_texto(texto: str) -> str:
    # Usa la logica de fuzzy_matcher para tener tokens consistentes
    return " ".join(_tokens(str(texto).lower()))

def _get_model_path(empresa_id: int) -> str:
    return os.path.join(_MODELS_DIR, f"modelo_empresa_{empresa_id}.joblib")

class EntrenadorML:
    @staticmethod
    def entrenar(empresa_id: int, datos_entrenamiento: list[dict]):
        """
        datos_entrenamiento: lista de dicts con 'descripcion', 'tipo' y 'regla_id'
        Solo entrena si hay mas de 10 ejemplos para evitar sobreajuste rapido.
        """
        if not _ML_DISPONIBLE or len(datos_entrenamiento) < 10:
            return False
            
        df = pd.DataFrame(datos_entrenamiento)
        # Limpiamos descripciones y concatenamos con el tipo (ingreso/egreso)
        df["feature"] = df["descripcion"].apply(limpiar_texto) + " " + df["tipo"]
        
        # Filtramos clases que tienen menos de 2 ejemplos para evitar warnings/errores en SGD (Cross-validation split issues, o imposibilidad de calibrar)
        conteos = df["regla_id"].value_counts()
        clases_validas = conteos[conteos >= 2].index
        df = df[df["regla_id"].isin(clases_validas)]
        
        if len(df) < 10 or len(df["regla_id"].unique()) < 2:
            return False

        pipeline = Pipeline([
            ('tfidf', TfidfVectorizer(ngram_range=(1, 2), min_df=1)),
            ('clf', SGDClassifier(loss='log_loss', penalty='l2', max_iter=1000, random_state=42)) # log_loss permite predict_proba
        ])
        
        pipeline.fit(df["feature"], df["regla_id"])
        
        # Guardar en cache
        joblib.dump(pipeline, _get_model_path(empresa_id))
        return True

class PredictorML:
    @staticmethod
    def predecir(empresa_id: int, descripcion: str, tipo: str, umbral: float = 0.88):
        """
        Intenta predecir la regla_id. Retorna (regla_id, probabilidad) si supera el umbral, sino (None, 0.0)
        """
        if not _ML_DISPONIBLE:
            return None, 0.0
        path = _get_model_path(empresa_id)
        if not os.path.exists(path):
            return None, 0.0
            
        try:
            modelo = joblib.load(path)
        except Exception:
            return None, 0.0
            
        feature = limpiar_texto(descripcion) + " " + tipo
        
        # Predecir
        proba = modelo.predict_proba([feature])[0]
        max_idx = proba.argmax()
        score = proba[max_idx]
        
        if score >= umbral:
            clase_predicha = modelo.classes_[max_idx]
            return int(clase_predicha), float(score)
            
        return None, float(score)

