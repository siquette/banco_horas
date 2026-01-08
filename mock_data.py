import pandas as pd
import random
from datetime import date, datetime as dt, timedelta

def gerar_dados_ficticios():
    """Gera 1 ano de dados simulados na memória."""
    ano_atual = date.today().year
    datas = pd.date_range(start=f'{ano_atual}-01-01', end=f'{ano_atual}-12-31')
    dados = []
    
    for d in datas:
        if d.weekday() >= 5: continue # Pula FDS
        
        # Gera horários com ruído aleatório
        min_ent = random.randint(-15, 45) 
        h_ent = dt(2000, 1, 1, 9, 0) + timedelta(minutes=min_ent)
        
        min_sai = random.randint(-20, 120) 
        h_sai = dt(2000, 1, 1, 18, 0) + timedelta(minutes=min_sai)
        
        h_ai = dt(2000, 1, 1, 12, 0) + timedelta(minutes=random.randint(0, 10))
        h_av = h_ai + timedelta(hours=1, minutes=random.randint(-5, 10))

        e_ini, e_fim = "00:00", "00:00"
        if random.random() < 0.2: # 20% de chance de extra em casa
            e_ini = "20:00"
            e_fim = (dt(2000,1,1,20,0) + timedelta(minutes=random.randint(30, 120))).strftime("%H:%M")

        dados.append({
            'data': d.strftime("%Y-%m-%d"),
            'entrada': h_ent.strftime("%H:%M"),
            'almoco_ida': h_ai.strftime("%H:%M"),
            'almoco_volta': h_av.strftime("%H:%M"),
            'saida': h_sai.strftime("%H:%M"),
            'extra_inicio': e_ini,
            'extra_fim': e_fim,
            'obs': "Dado Simulado (Modo Demo)",
            'feriado_manual': 0
        })
    return pd.DataFrame(dados)