import streamlit as st
from sqlalchemy import text

# --- CAMADA DE DADOS (POSTGRESQL / NEON) ---
@st.cache_resource
def get_db_connection():
    return st.connection("postgresql", type="sql")

def init_db():
    """Inicializa a tabela e roda migrações se necessário."""
    conn = get_db_connection()
    with conn.session as s:
        s.execute(text('''
            CREATE TABLE IF NOT EXISTS registros (
                data TEXT PRIMARY KEY,
                entrada TEXT,
                almoco_ida TEXT,
                almoco_volta TEXT,
                saida TEXT,
                extra_inicio TEXT, 
                extra_fim TEXT,
                obs TEXT,
                feriado_manual INTEGER DEFAULT 0,
                home_office INTEGER DEFAULT 0
            );
        '''))
        s.commit()
        
        # Migrações silenciosas
        colunas_novas = [
            "ALTER TABLE registros ADD COLUMN extra_inicio TEXT;",
            "ALTER TABLE registros ADD COLUMN extra_fim TEXT;",
            "ALTER TABLE registros ADD COLUMN home_office INTEGER DEFAULT 0;"
        ]
        for sql in colunas_novas:
            try:
                s.execute(text(sql))
                s.commit()
            except:
                s.rollback()

def salvar_registro(data, entrada, a_ida, a_volta, saida, ext_ini, ext_fim, obs, is_feriado, is_home_office):
    conn = get_db_connection()
    feriado_int = 1 if is_feriado else 0
    home_office_int = 1 if is_home_office else 0
    
    sql = text('''
        INSERT INTO registros (data, entrada, almoco_ida, almoco_volta, saida, extra_inicio, extra_fim, obs, feriado_manual, home_office)
        VALUES (:data, :ent, :ai, :av, :sai, :ei, :ef, :obs, :fer, :ho)
        ON CONFLICT (data) DO UPDATE SET
            entrada = EXCLUDED.entrada,
            almoco_ida = EXCLUDED.almoco_ida,
            almoco_volta = EXCLUDED.almoco_volta,
            saida = EXCLUDED.saida,
            extra_inicio = EXCLUDED.extra_inicio,
            extra_fim = EXCLUDED.extra_fim,
            obs = EXCLUDED.obs,
            feriado_manual = EXCLUDED.feriado_manual,
            home_office = EXCLUDED.home_office;
    ''')
    
    params = {
        "data": data, "ent": str(entrada), "ai": str(a_ida), "av": str(a_volta), "sai": str(saida),
        "ei": str(ext_ini), "ef": str(ext_fim), "obs": obs, "fer": feriado_int, "ho": home_office_int
    }
    
    with conn.session as s:
        s.execute(sql, params)
        s.commit()
    
    # Limpa o cache do Streamlit para atualizar os gráficos na hora
    st.cache_data.clear()

def carregar_dados():
    conn = get_db_connection()
    return conn.query("SELECT * FROM registros", ttl=0)

def excluir_registro(data_str):
    conn = get_db_connection()
    with conn.session as s:
        s.execute(text("DELETE FROM registros WHERE data = :d"), {"d": data_str})
        s.commit()
    st.cache_data.clear()