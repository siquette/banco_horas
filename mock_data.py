import pandas as pd
import random
from datetime import date, datetime as dt, timedelta
import holidays

def gerar_dados_ficticios(cenario="superavit"):
    """
    Gera dados simulados.
    Cenarios:
    - 'superavit': Workaholic (muita hora extra, mas descansa FDS).
    - 'deficit': Desmotivado (atrasos e faltas).
    - 'teste_feriado': O CRÍTICO. Trabalha Feriados e Finais de Semana (Burnout).
    """
    ano_atual = date.today().year
    datas = pd.date_range(start=f'{ano_atual}-01-01', end=f'{ano_atual}-12-31')
    dados = []
    
    # Calendário de Feriados para referência (pra garantir que vamos trabalhar neles)
    br_holidays = holidays.BR(subdiv='SP')
    
    for d in datas:
        data_str = d.strftime("%Y-%m-%d")
        is_fds = d.weekday() >= 5
        is_feriado = data_str in br_holidays
        
        # --- FILTRO DE DIAS ÚTEIS ---
        # Se NÃO for o cenário de teste de feriado, pula o FDS normalmente.
        if cenario != "teste_feriado" and is_fds:
            continue 
            
        # --- LÓGICA DOS PERFIS ---
        if cenario == "teste_feriado":
            # Perfil "Plantão / Crise"
            # Trabalha todo dia.
            # Se for Feriado ou FDS, faz jornada reduzida mas conta dobrado/1.5x
            range_entrada = (-10, 10)  # Chega 09:00 em ponto
            range_saida = (0, 60)      # Sai 18:00 ou 19:00
            chance_extra_casa = 0.5    # Leva muito trabalho pra casa
            chance_falta = 0.0
            chance_ho = 0.2
            
        elif cenario == "superavit":
            range_entrada = (-45, 5)
            range_saida = (30, 120)
            chance_extra_casa = 0.4
            chance_falta = 0.0
            chance_ho = 0.2
            
        elif cenario == "deficit":
            range_entrada = (15, 90)
            range_saida = (-120, -10)
            chance_extra_casa = 0.01
            chance_falta = 0.10
            chance_ho = 0.5
        else:
            # Default
            range_entrada = (-10, 10)
            range_saida = (-10, 10)
            chance_extra_casa = 0.1
            chance_falta = 0.01
            chance_ho = 0.2

        # --- GERAÇÃO DO REGISTRO ---

        # 1. Aplica Falta (Sorteio)
        if random.random() < chance_falta:
            dados.append({
                'data': data_str,
                'entrada': "00:00", 'almoco_ida': "00:00", 'almoco_volta': "00:00", 'saida': "00:00",
                'extra_inicio': "00:00", 'extra_fim': "00:00",
                'obs': f"Falta Simulada ({cenario})",
                'feriado_manual': 0, 'home_office': 0
            })
            continue

        # 2. Gera Horários
        min_ent = random.randint(range_entrada[0], range_entrada[1])
        h_ent = dt(2000, 1, 1, 9, 0) + timedelta(minutes=min_ent)
        
        min_sai = random.randint(range_saida[0], range_saida[1])
        h_sai = dt(2000, 1, 1, 18, 0) + timedelta(minutes=min_sai)
        
        # Almoço
        h_ai = dt(2000, 1, 1, 12, 0) + timedelta(minutes=random.randint(0, 10))
        h_av = h_ai + timedelta(hours=1, minutes=random.randint(-5, 10))

        # 3. Gera Extra Casa
        e_ini_str, e_fim_str = "00:00", "00:00"
        if random.random() < chance_extra_casa:
            inicio_base = 20 if h_sai.hour < 20 else h_sai.hour + 1
            if inicio_base >= 23: inicio_base = 23
            h_extra_ini = dt(2000, 1, 1, inicio_base, 0) + timedelta(minutes=random.randint(0, 30))
            h_extra_fim = h_extra_ini + timedelta(minutes=random.randint(45, 150))
            e_ini_str = h_extra_ini.strftime("%H:%M")
            e_fim_str = h_extra_fim.strftime("%H:%M")

        is_ho = 1 if random.random() < chance_ho else 0
        
        # Obs especial para identificar no Grid
        obs_texto = f"Simulação ({cenario})"
        if cenario == "teste_feriado" and (is_fds or is_feriado):
            obs_texto = "🔥 PLANTÃO FDS/FERIADO"

        dados.append({
            'data': data_str,
            'entrada': h_ent.strftime("%H:%M"),
            'almoco_ida': h_ai.strftime("%H:%M"),
            'almoco_volta': h_av.strftime("%H:%M"),
            'saida': h_sai.strftime("%H:%M"),
            'extra_inicio': e_ini_str, 'extra_fim': e_fim_str,
            'obs': obs_texto,
            'feriado_manual': 0, 'home_office': is_ho
        })
        
    return pd.DataFrame(dados)