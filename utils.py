import streamlit as st
import pandas as pd
from datetime import date, timedelta
import holidays
from io import BytesIO
from typing import Tuple, Optional

# Constante Global
META_DIARIA = 8.0 

# --- ENGENHARIA DE CALENDÁRIO (SP CAPITAL) ---
def obter_feriados_sp(ano: int):
    """
    Retorna os feriados da Cidade de São Paulo (Capital).
    Combina: Feriados BR + Feriados Estaduais SP + Aniversário de SP.
    """
    # 1. Pega feriados Nacionais e Estaduais (SP)
    br_holidays = holidays.BR(subdiv='SP', years=ano)
    
    # 2. Adiciona Feriados Municipais (São Paulo Capital)
    # 25 de Janeiro: Aniversário de São Paulo
    br_holidays.append({f"{ano}-01-25": "Aniversário de São Paulo"})
    
    # Corpus Christi (Móvel - geralmente a lib calcula, mas garantimos aqui se falhar)
    # A lib 'holidays' geralmente já inclui Corpus Christi para BR, mas como ponto facultativo.
    # Em SP capital é Feriado Municipal oficial.
    
    # Consciência Negra (20/11) agora é Nacional, então a lib já traz.
    
    return br_holidays

# --- HELPERS DE TEMPO ---
def parse_db_time_to_delta(time_str: Optional[str]) -> timedelta:
    if pd.isna(time_str) or str(time_str).strip() in ['None', '']: return timedelta(0)
    try:
        parts = list(map(int, str(time_str).split(':')))
        if len(parts) == 2: return timedelta(hours=parts[0], minutes=parts[1])
        elif len(parts) == 3: return timedelta(hours=parts[0], minutes=parts[1], seconds=parts[2])
        return timedelta(0)
    except ValueError: return timedelta(0)

def calcular_delta_com_virada(inicio_str: Optional[str], fim_str: Optional[str]) -> float:
    t_ini = parse_db_time_to_delta(inicio_str)
    t_fim = parse_db_time_to_delta(fim_str)
    if t_ini == t_fim: return 0.0
    delta = t_fim - t_ini
    if delta.total_seconds() < 0: delta += timedelta(days=1)
    return delta.total_seconds() / 3600.0

def definir_meta(row: pd.Series) -> Tuple[float, str]:
    feriado_manual = int(row.get('feriado_manual', 0))
    data_str = str(row['data'])
    data_dt = row['data_dt']
    
    # 1. Prioridade: Feriado Manual (Override do usuário)
    if feriado_manual == 1:
        return 0.0, "Folga Manual"

    # 2. Calendário Inteligente (SP Capital)
    # Instancia os feriados para o ano da data específica
    ano_data = data_dt.year
    feriados_sp = obter_feriados_sp(ano_data)
    
    if data_str in feriados_sp:
        nome_feriado = feriados_sp.get(data_str)
        return 0.0, f"Feriado ({nome_feriado})"
        
    # 3. Fim de Semana
    if pd.notnull(data_dt):
        if data_dt.weekday() == 6: return 0.0, "Domingo"
        if data_dt.weekday() == 5: return 0.0, "Sábado"
        
    return META_DIARIA, "Dia Útil"

# --- PROCESSAMENTO PRINCIPAL ---
@st.cache_data(show_spinner=False) 
def processar_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty: return df
    df['data_dt'] = pd.to_datetime(df['data'], errors='coerce')
    
    # 1. Durações Brutas
    cols_tempo = ['entrada', 'saida', 'almoco_ida', 'almoco_volta']
    for col in cols_tempo:
        df[f'td_{col}'] = df[col].apply(parse_db_time_to_delta)

    jornada_bruta = df['td_saida'] - df['td_entrada']
    pausa_almoco = df['td_almoco_volta'] - df['td_almoco_ida']
    
    df['horas_principal'] = (jornada_bruta - pausa_almoco).dt.total_seconds() / 3600.0
    df['horas_principal'] = df['horas_principal'].fillna(0.0)
    
    df['horas_extra_campo'] = df.apply(lambda x: calcular_delta_com_virada(x['extra_inicio'], x['extra_fim']), axis=1)
    df['horas_extra_campo'] = df['horas_extra_campo'].fillna(0.0)
    
    # 2. Distribuição Geográfica
    def distribuir_horas(row):
        is_ho = row.get('home_office', 0) == 1
        principal = row['horas_principal']
        extra = row['horas_extra_campo']
        if is_ho: return 0.0, principal + extra
        else: return principal, extra

    df[['horas_escritorio', 'horas_casa']] = df.apply(distribuir_horas, axis=1, result_type='expand')
    df['total_trabalhado'] = df['horas_escritorio'] + df['horas_casa']
    
    # 3. Metas e Motivos (Usando Calendário SP)
    meta_info = df.apply(lambda r: definir_meta(r), axis=1)
    df['meta_calculada'] = meta_info.apply(lambda x: x[0])
    df['motivo_dia'] = meta_info.apply(lambda x: x[1])

    # 4. Cálculo de Extras com MULTIPLICADOR TRIFÁSICO
    def calcular_extras_com_peso(row):
        saldo_bruto = row['total_trabalhado'] - row['meta_calculada']
        motivo = str(row['motivo_dia']).lower()
        
        if saldo_bruto <= 0:
            return 0.0, 0.0, saldo_bruto 
            
        # Regra de Pesos (CLT + Convenção)
        if "domingo" in motivo or "feriado" in motivo:
            peso = 2.0 # 100%
        elif "sábado" in motivo:
            peso = 1.5 # 50%
        else:
            peso = 1.0 # Dia Útil
        
        saldo_com_peso = saldo_bruto * peso
        
        meta = row['meta_calculada']
        h_esc = row['horas_escritorio']
        
        if h_esc > meta:
            extra_esc = (h_esc - meta) * peso
            extra_casa = row['horas_casa'] * peso
        else:
            extra_esc = 0.0
            extra_casa = saldo_com_peso
            
        return extra_esc, extra_casa, saldo_com_peso

    df[['extra_escritorio', 'extra_casa', 'saldo']] = df.apply(calcular_extras_com_peso, axis=1, result_type='expand')

    cols_float = ['horas_escritorio', 'horas_casa', 'total_trabalhado', 
                  'horas_principal', 'horas_extra_campo', 'extra_escritorio', 'extra_casa', 'saldo']
    df[cols_float] = df[cols_float].round(2)
    
    return df

def to_excel(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Ponto')
        worksheet = writer.sheets['Ponto']
        worksheet.set_column('A:A', 12)
        worksheet.set_column('B:K', 10)
    return output.getvalue()

# --- VALIDAÇÃO DE INTEGRIDADE (NOVA FUNÇÃO) ---
def validar_registro(entrada, almoco_ida, almoco_volta, saida, is_falta):
    """
    Verifica se os horários fazem sentido lógico.
    Retorna: (bool, str) -> (Passou?, Mensagem de Erro)
    """
    # 1. Se for falta, tudo bem estar zerado
    if is_falta:
        return True, ""

    # 2. Converte para timedelta para comparar
    def td(t): return timedelta(hours=t.hour, minutes=t.minute)
    
    t_ent = td(entrada)
    t_sai = td(saida)
    t_ai = td(almoco_ida)
    t_av = td(almoco_volta)
    
    # Regra A: Saída deve ser depois da Entrada (Considerando mesmo dia)
    # Nota: Se seu sistema aceita virada de noite (ex: entra 22h sai 05h), 
    # essa validação precisa ser mais complexa. Assumindo dia comercial aqui:
    if t_sai <= t_ent and t_sai != timedelta(0): 
        # Aceitamos saida 00:00 como "não bateu ainda", mas se preencheu, tem que ser maior
        return False, "❌ A Saída não pode ser anterior à Entrada!"

    # Regra B: Almoço deve estar 'dentro' da jornada
    if t_ai < t_ent or t_ai > t_sai:
        return False, "❌ O horário de almoço deve estar entre a Entrada e a Saída."

    # Regra C: Volta do almoço deve ser depois da ida
    if t_av <= t_ai:
        return False, "❌ A volta do almoço deve ser depois da ida."
        
    # Regra D: Almoço Mínimo (CLT - 1 hora) - Opcional, vamos por como aviso
    tempo_almoco = (t_av - t_ai).total_seconds() / 3600
    if tempo_almoco < 1.0 and tempo_almoco > 0:
        # Não bloqueia, mas é bom saber. (Aqui vamos retornar True, mas poderia ser warning)
        pass 

    return True, ""