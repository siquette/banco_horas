import streamlit as st
import pandas as pd
from datetime import date, time, datetime as dt, timedelta
import holidays
import plotly.express as px
from io import BytesIO
from typing import Tuple, Optional
from sqlalchemy import text 

# --- IMPORT NOVO (Refatoração) ---
from mock_data import gerar_dados_ficticios

# --- CONFIGURAÇÕES GERAIS ---
st.set_page_config(page_title="Gestão de Tempo Analytics", layout="wide", page_icon="📊")
META_DIARIA = 8.0 

# --- AUTENTICAÇÃO ---
def check_password():
    """Retorna True se o usuário tiver a senha correta."""
    def password_entered():
        if st.session_state["password"] == st.secrets["geral"]["senha_acesso"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if st.session_state.get("password_correct", False):
        return True

    st.text_input(
        "🔒 Digite a senha para acessar:", 
        type="password", 
        on_change=password_entered, 
        key="password"
    )
    
    if "password_correct" in st.session_state:
        st.error("😕 Senha incorreta")
        
    return False

if not check_password():
    st.stop()

# --- CAMADA DE DADOS (POSTGRESQL / NEON) ---
@st.cache_resource
def get_db_connection():
    return st.connection("postgresql", type="sql")

def init_db():
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
                feriado_manual INTEGER DEFAULT 0
            );
        '''))
        s.commit()

        try:
            s.execute(text("ALTER TABLE registros ADD COLUMN extra_inicio TEXT;"))
            s.commit()
        except:
            s.rollback()

        try:
            s.execute(text("ALTER TABLE registros ADD COLUMN extra_fim TEXT;"))
            s.commit()
        except:
            s.rollback()

def salvar_registro(data, entrada, a_ida, a_volta, saida, ext_ini, ext_fim, obs, is_feriado):
    conn = get_db_connection()
    feriado_int = 1 if is_feriado else 0
    
    sql = text('''
        INSERT INTO registros (data, entrada, almoco_ida, almoco_volta, saida, extra_inicio, extra_fim, obs, feriado_manual)
        VALUES (:data, :ent, :ai, :av, :sai, :ei, :ef, :obs, :fer)
        ON CONFLICT (data) DO UPDATE SET
            entrada = EXCLUDED.entrada,
            almoco_ida = EXCLUDED.almoco_ida,
            almoco_volta = EXCLUDED.almoco_volta,
            saida = EXCLUDED.saida,
            extra_inicio = EXCLUDED.extra_inicio,
            extra_fim = EXCLUDED.extra_fim,
            obs = EXCLUDED.obs,
            feriado_manual = EXCLUDED.feriado_manual;
    ''')
    
    params = {
        "data": data,
        "ent": str(entrada),
        "ai": str(a_ida),
        "av": str(a_volta),
        "sai": str(saida),
        "ei": str(ext_ini),
        "ef": str(ext_fim),
        "obs": obs,
        "fer": feriado_int
    }
    
    with conn.session as s:
        s.execute(sql, params)
        s.commit()

def carregar_dados():
    conn = get_db_connection()
    return conn.query("SELECT * FROM registros", ttl=0)

def excluir_registro(data_str):
    conn = get_db_connection()
    with conn.session as s:
        s.execute(text("DELETE FROM registros WHERE data = :d"), {"d": data_str})
        s.commit()

# --- FUNÇÃO HELPER (EXCEL) ---
def to_excel(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Ponto')
        worksheet = writer.sheets['Ponto']
        worksheet.set_column('A:A', 12)
        worksheet.set_column('B:G', 10)
    return output.getvalue()

# --- LÓGICA DE NEGÓCIO ---
def parse_db_time_to_delta(time_str: Optional[str]) -> timedelta:
    if pd.isna(time_str) or str(time_str).strip() in ['None', '']:
        return timedelta(0)
    try:
        parts = list(map(int, str(time_str).split(':')))
        if len(parts) == 2: return timedelta(hours=parts[0], minutes=parts[1])
        elif len(parts) == 3: return timedelta(hours=parts[0], minutes=parts[1], seconds=parts[2])
        return timedelta(0)
    except ValueError:
        return timedelta(0)

def calcular_delta_com_virada(inicio_str: Optional[str], fim_str: Optional[str]) -> float:
    t_ini = parse_db_time_to_delta(inicio_str)
    t_fim = parse_db_time_to_delta(fim_str)
    if t_ini == t_fim: return 0.0
    delta = t_fim - t_ini
    if delta.total_seconds() < 0:
        delta += timedelta(days=1)
    return delta.total_seconds() / 3600.0

def processar_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty: return df
    df['data_dt'] = pd.to_datetime(df['data'], errors='coerce')
    cols_tempo = ['entrada', 'saida', 'almoco_ida', 'almoco_volta']
    for col in cols_tempo:
        df[f'td_{col}'] = df[col].apply(parse_db_time_to_delta)

    jornada_bruta = df['td_saida'] - df['td_entrada']
    pausa_almoco = df['td_almoco_volta'] - df['td_almoco_ida']
    df['horas_escritorio'] = (jornada_bruta - pausa_almoco).dt.total_seconds() / 3600.0
    
    df['horas_casa'] = df.apply(lambda x: calcular_delta_com_virada(x['extra_inicio'], x['extra_fim']), axis=1)
    
    df['horas_escritorio'] = df['horas_escritorio'].fillna(0.0)
    df['horas_casa'] = df['horas_casa'].fillna(0.0)
    df['total_trabalhado'] = df['horas_escritorio'] + df['horas_casa']
    
    cols_float = ['horas_escritorio', 'horas_casa', 'total_trabalhado']
    df[cols_float] = df[cols_float].astype(float)
    return df

def definir_meta(row: pd.Series) -> Tuple[float, str]:
    feriado_manual = int(row.get('feriado_manual', 0))
    data_str = str(row['data'])
    data_dt = row['data_dt']
    if feriado_manual == 1: return 0.0, "Folga/Feriado Manual"
    br_holidays = holidays.BR()
    if data_str in br_holidays: return 0.0, f"Feriado: {br_holidays.get(data_str)}"
    if pd.notnull(data_dt) and data_dt.weekday() >= 5: return 0.0, "Fim de Semana"
    return META_DIARIA, "Dia Útil"

# --- MAIN EXECUTION ---
try:
    init_db()
except Exception as e:
    # Apenas loga, não para a execução (importante para modo demo funcionar sem rede)
    print(f"Aviso conexão: {e}")


# --- SIDEBAR E MODO DEMO ---
with st.sidebar:
    st.header("⚙️ Configurações")
    modo_demo = st.toggle("🧪 Modo Demonstração", help="Gera dados fictícios para testar os gráficos")
    
    if modo_demo:
        st.warning("⚠️ Visualizando Dados Fictícios")
        df_bd = gerar_dados_ficticios()
        
        # --- LINHA DE DEBUG (Adicione aqui) ---
        st.write(f"🐞 Debug: {len(df_bd)} linhas geradas")
        # --------------------------------------
        
    else:
        try:
            df_bd = carregar_dados()
        except Exception as e:
            st.error(f"Erro ao carregar banco: {e}")
            df_bd = pd.DataFrame()
            
tab_lancamento, tab_analytics = st.tabs(["📝 Lançamento & Extrato", "📈 Análise Gerencial (BI)"])

# ---------------- ABA 1: LANÇAMENTO (ATUALIZADA PARA FALTAS) ----------------
with tab_lancamento:
    st.title("Apontamento Diário")
    col_input, col_view = st.columns([1, 2])
    
    with col_input:
        with st.container(border=True):
            st.subheader("Novo Registro")
            data_sel = st.date_input("Data", date.today())
            
            # Recupera dados se já existirem
            if not df_bd.empty:
                rec = df_bd[df_bd['data'] == str(data_sel)]
            else:
                rec = pd.DataFrame()

            # --- LÓGICA DE VALORES PADRÃO ---
            # Horários padrão
            d_ent, d_sai = time(9,0), time(18,0)
            d_ai, d_av = time(12,0), time(13,0)
            d_ext_ini, d_ext_fim = time(0,0), time(0,0)
            d_feriado, d_obs = False, ""
            d_falta = False # Novo estado padrão

            if not rec.empty:
                st.info("Editando dia existente")
                # Lógica para recuperar se foi falta (se tudo for 00:00 e obs tiver 'Falta')
                d_obs = rec.iloc[0]['obs']
                e_str = rec.iloc[0]['entrada']
                s_str = rec.iloc[0]['saida']
                
                # Se entrada e saída forem 00:00 e não for feriado manual, é falta
                if e_str == "00:00:00" and s_str == "00:00:00": 
                    d_falta = True
                
                try: d_feriado = True if rec.iloc[0]['feriado_manual'] == 1 else False
                except: d_feriado = False
                
                # Recupera horários do banco (se não for falta)
                if not d_falta:
                    try:
                        h, m, s = map(int, rec.iloc[0]['extra_inicio'].split(':'))
                        d_ext_ini = time(h, m)
                        h, m, s = map(int, rec.iloc[0]['extra_fim'].split(':'))
                        d_ext_fim = time(h, m)
                        # ... recupera outros horários aqui se necessário, 
                        # mas os inputs abaixo já pegam o padrão se não mudarmos
                    except: pass

            # --- CONTROLES DE CHECKBOX ---
            # Organizando em colunas para economizar espaço
            ck1, ck2 = st.columns(2)
            is_feriado = ck1.checkbox("Feriado/Folga? (Meta 0h)", value=d_feriado, help="Não desconta horas do banco.")
            is_falta = ck2.checkbox("Falta? (Descontar 8h)", value=d_falta, help="Zera o trabalho e desconta a meta do banco.")

            # --- LÓGICA DE BLOQUEIO E ZERAGEM ---
            # Se for falta, forçamos horário zero. Se for feriado, desabilitamos.
            disable_inputs = modo_demo or is_feriado or is_falta
            
            if is_falta:
                # Força visualmente os horários para zero
                d_ent, d_sai = time(0,0), time(0,0)
                d_ai, d_av = time(0,0), time(0,0)
                if d_obs == "": d_obs = "Falta injustificada / Não trabalhado"
            
            c1, c2 = st.columns(2)
            entrada = c1.time_input("Entrada", d_ent, disabled=disable_inputs)
            saida = c2.time_input("Saída", d_sai, disabled=disable_inputs)
            c3, c4 = st.columns(2)
            almoco_ida = c3.time_input("Almoço Ida", d_ai, disabled=disable_inputs)
            almoco_volta = c4.time_input("Almoço Volta", d_av, disabled=disable_inputs)
            
            st.caption("Trabalho Extra (Casa)")
            c5, c6 = st.columns(2)
            ext_ini = c5.time_input("Início Extra", d_ext_ini, disabled=modo_demo)
            ext_fim = c6.time_input("Fim Extra", d_ext_fim, disabled=modo_demo)
            obs = st.text_area("Obs", value=d_obs, height=68, disabled=modo_demo)
            
            if modo_demo:
                st.info("🔒 Edição bloqueada no Modo Demo")
            else:
                if st.button("Salvar Registro", type="primary", use_container_width=True):
                    # Se for falta, garantimos que salvamos 00:00 no banco, independente do input
                    if is_falta:
                        entrada = almoco_ida = almoco_volta = saida = time(0,0)
                        ext_ini = ext_fim = time(0,0)
                    
                    salvar_registro(str(data_sel), entrada, almoco_ida, almoco_volta, saida, ext_ini, ext_fim, obs, disable_inputs)
                    st.toast("Registro Salvo com Sucesso!")
                    st.rerun()

            st.markdown("---")
            with st.expander("🗑️ Excluir / Corrigir Data Errada"):
                if not df_bd.empty and not modo_demo:
                    lista_datas = df_bd['data'].sort_values(ascending=False).tolist()
                    data_para_excluir = st.selectbox("Selecione o dia para apagar:", options=lista_datas)
                    if st.button("🗑️ Apagar Registro Selecionado", type="secondary", use_container_width=True):
                        excluir_registro(data_para_excluir)
                        st.success(f"Registro de {data_para_excluir} apagado!")
                        st.rerun()
                elif modo_demo:
                    st.info("Exclusão desativada no Modo Demo.")
                else:
                    st.info("Não há registros para excluir.")

    with col_view:
        if not df_bd.empty:
            df = processar_dataframe(df_bd)
            df[['meta', 'motivo']] = df.apply(definir_meta, axis=1, result_type='expand')
            df['saldo'] = df['total_trabalhado'] - df['meta']
            
            c_kpi1, c_kpi2, c_kpi3 = st.columns(3)
            saldo_total = df['saldo'].sum()
            # Cor do KPI muda se estiver negativo
            c_kpi1.metric("Banco de Horas", f"{saldo_total:+.2f} h", delta_color="normal" if saldo_total >= 0 else "inverse")
            c_kpi2.metric("Total Extra (Casa)", f"{df['horas_casa'].sum():.2f} h")
            c_kpi3.metric("Dias Registrados", len(df))
            
            st.dataframe(
                df[['data', 'horas_escritorio', 'horas_casa', 'total_trabalhado', 'saldo', 'motivo']]
                .sort_values('data', ascending=False)
                .style.format("{:.2f}", subset=['horas_escritorio', 'horas_casa', 'total_trabalhado', 'saldo'])
                .background_gradient(subset=['saldo'], cmap='RdYlGn', vmin=-8, vmax=8),
                use_container_width=True
            )
            st.download_button("📥 Baixar Excel Completo", to_excel(df), "ponto_completo.xlsx")
        else:
            st.warning("Sem dados para exibir.")

# ---------------- ABA 2: ANALYTICS ----------------
with tab_analytics:
    st.header("Análise Gerencial & BI")
    
    # Verifica se tem dados ANTES de tentar plotar qualquer coisa
    if not df_bd.empty:
        df = processar_dataframe(df_bd)
        df[['meta', 'motivo']] = df.apply(definir_meta, axis=1, result_type='expand')
        df['saldo'] = df['total_trabalhado'] - df['meta']
        
        st.markdown("### 🔍 Filtros")
        col_f1, col_f2 = st.columns(2)
        min_date = df['data_dt'].min().date()
        max_date = df['data_dt'].max().date()
        range_sel = col_f1.date_input("Período", value=(min_date, max_date))
        
        if isinstance(range_sel, tuple) and len(range_sel) == 2:
            start_d, end_d = range_sel
            mask = (df['data_dt'].dt.date >= start_d) & (df['data_dt'].dt.date <= end_d)
            df_filtered = df.loc[mask].copy()
        else:
            df_filtered = df.copy()

        st.markdown("---")

        # 1. HEATMAP ESTILO GITHUB
        st.subheader("📅 Mapa de Contribuições (GitHub Style)")
        df_filtered['week'] = df_filtered['data_dt'].dt.isocalendar().week
        df_filtered['weekday_num'] = df_filtered['data_dt'].dt.weekday
        df_filtered['year'] = df_filtered['data_dt'].dt.year
        
        heatmap_data = df_filtered.groupby(['year', 'week', 'weekday_num'])['total_trabalhado'].sum().reset_index()
        
        import plotly.graph_objects as go
        fig_git = go.Figure(data=go.Heatmap(
            z=heatmap_data['total_trabalhado'],
            x=heatmap_data['week'],
            y=heatmap_data['weekday_num'],
            colorscale='Greens',
            xgap=3, ygap=3, hoverongaps=False,
            hovertemplate="Semana: %{x}<br>Dia: %{y}<br>Horas: %{z:.2f}h<extra></extra>"
        ))
        fig_git.update_layout(
            title="Intensidade de Trabalho por Semana",
            plot_bgcolor='rgba(0,0,0,0)', height=250,
            yaxis=dict(tickmode='array', tickvals=[0, 1, 2, 3, 4, 5, 6], ticktext=['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom'], autorange='reversed', title=""),
            xaxis=dict(title="Semana do Ano", showgrid=False),
            margin=dict(l=40, r=40, t=40, b=40)
        )
        st.plotly_chart(fig_git, use_container_width=True)

        # 2. GRÁFICO DE BARRAS
        st.subheader("📊 Composição Diária")
        df_bar = df_filtered.sort_values('data_dt')
        fig_bar = px.bar(
            df_bar, x='data', y=['horas_escritorio', 'horas_casa'], 
            title="Horas Diárias (Com Rótulos)",
            labels={'value': 'Horas', 'variable': 'Local', 'data': 'Data'},
            color_discrete_map={'horas_escritorio': '#3498DB', 'horas_casa': '#E67E22'},
            text_auto='.1f'
        )
        fig_bar.add_hline(y=META_DIARIA, line_dash="dot", line_color="red", annotation_text="Meta 8h")
        fig_bar.update_layout(hovermode="x unified", barmode='stack')
        st.plotly_chart(fig_bar, use_container_width=True)

        # 3. TENDÊNCIA E PIZZA
        c_graf1, c_graf2 = st.columns(2)
        with c_graf1:
            st.subheader("📈 Evolução do Banco")
            df_filtered = df_filtered.sort_values('data_dt')
            df_filtered['saldo_acumulado'] = df_filtered['saldo'].cumsum()
            fig_line = px.line(df_filtered, x='data', y='saldo_acumulado', markers=True, title="Saldo Acumulado", line_shape="spline")
            fig_line.add_hline(y=0, line_dash="dot", line_color="gray")
            if not df_filtered.empty:
                cor_linha = "green" if df_filtered['saldo_acumulado'].iloc[-1] >= 0 else "red"
                fig_line.update_traces(line_color=cor_linha)
            st.plotly_chart(fig_line, use_container_width=True)
            
        with c_graf2:
            st.subheader("🥧 Distribuição Total")
            fig_pie = px.pie(
                names=["Escritório", "Extra Casa"],
                values=[df_filtered['horas_escritorio'].sum(), df_filtered['horas_casa'].sum()],
                hole=0.4, title="Proporção de Trabalho",
                color_discrete_sequence=['#3498DB', '#E67E22']
            )
            st.plotly_chart(fig_pie, use_container_width=True)

        # 4. COMPORTAMENTO
        st.subheader("🧩 Padrão de Comportamento")
        def time_to_float(t_str):
            if pd.isna(t_str): return None
            try:
                parts = list(map(int, str(t_str).split(':')))
                if len(parts) == 3: return parts[0] + (parts[1]/60)
                elif len(parts) == 2: return parts[0] + (parts[1]/60)
                return None
            except: return None

        df_filtered['entrada_num'] = df_filtered['entrada'].apply(time_to_float)
        fig_scatter = px.scatter(
            df_filtered, x="entrada_num", y="total_trabalhado", color="saldo",
            size="total_trabalhado", hover_data=['data'], color_continuous_scale="RdYlGn",
            title="Chegada vs. Produtividade"
        )
        fig_scatter.add_vline(x=9.0, line_dash="dot", annotation_text="09:00")
        fig_scatter.add_hline(y=META_DIARIA, line_dash="dot", annotation_text="Meta")
        st.plotly_chart(fig_scatter, use_container_width=True)

        st.markdown("---")
        
        # 5. VIOLIN PLOT
        st.subheader("🎻 Distribuição por Dia (Violin)")
        df_filtered['weekday_name'] = df_filtered['data_dt'].dt.strftime("%A")
        dias_traducao = {'Monday': 'Segunda', 'Tuesday': 'Terça', 'Wednesday': 'Quarta', 'Thursday': 'Quinta', 'Friday': 'Sexta', 'Saturday': 'Sábado', 'Sunday': 'Domingo'}
        df_filtered['dia_pt'] = df_filtered['weekday_name'].map(dias_traducao).fillna(df_filtered['weekday_name'])
        ordem_dias = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
        
        fig_violin = px.violin(
            df_filtered, y="total_trabalhado", x="dia_pt", 
            box=True, points="all", hover_data=['data'], color="dia_pt",
            title="Densidade Semanal", category_orders={"dia_pt": ordem_dias}
        )
        fig_violin.add_hline(y=META_DIARIA, line_dash="dot", line_color="red")
        st.plotly_chart(fig_violin, use_container_width=True)

        # 6. TENDÊNCIA SUAVIZADA
        st.subheader("🌊 Tendência (Média Móvel 7 Dias)")
        df_rolling = df_filtered.sort_values('data_dt').set_index('data_dt')
        df_rolling['media_movel_7d'] = df_rolling['total_trabalhado'].rolling(window=7, min_periods=1).mean()
        df_rolling = df_rolling.reset_index()
        
        fig_trend = px.line(
            df_rolling, x='data_dt', y=['total_trabalhado', 'media_movel_7d'],
            title="Ruído vs. Tendência",
            color_discrete_map={'total_trabalhado': 'lightgray', 'media_movel_7d': 'blue'}
        )
        fig_trend.update_traces(selector={'name': 'total_trabalhado'}, line=dict(width=1, dash='dot'))
        fig_trend.update_traces(selector={'name': 'media_movel_7d'}, line=dict(width=4))
        fig_trend.add_hline(y=META_DIARIA, line_color="red")
        st.plotly_chart(fig_trend, use_container_width=True)

        st.markdown("---")
        
        # --- NOVOS GRÁFICOS (Agora DENTRO do if) ---
        c_adv1, c_adv2 = st.columns(2)

        # 8. ANÁLISE COMPARATIVA (TERRITÓRIO)
        with c_adv1:
            st.subheader("🏠 Território: Escritório vs. Híbrido")
            def classificar_dia(row):
                return "Dia Híbrido/Extra" if row['horas_casa'] > 0 else "Dia 100% Presencial"
            
            df_filtered['tipo_jornada'] = df_filtered.apply(classificar_dia, axis=1)
            fig_comp = px.box(
                df_filtered, x="tipo_jornada", y="total_trabalhado", color="tipo_jornada",
                title="Onde você trabalha mais?",
                color_discrete_map={'Dia 100% Presencial': '#3498DB', 'Dia Híbrido/Extra': '#E67E22'}
            )
            fig_comp.add_hline(y=META_DIARIA, line_dash="dot", annotation_text="Meta")
            st.plotly_chart(fig_comp, use_container_width=True)

        # 9. HISTOGRAMA DE PONTUALIDADE
        with c_adv2:
            st.subheader("⏰ Consistência de Chegada")
            fig_hist = px.histogram(
                df_filtered, x="entrada_num", nbins=20, 
                title="Distribuição do Horário de Chegada",
                labels={'entrada_num': 'Hora de Chegada'}, color_discrete_sequence=['#9B59B6']
            )
            media_chegada = df_filtered['entrada_num'].mean()
            if pd.notnull(media_chegada):
                h_media = int(media_chegada)
                m_media = int((media_chegada - h_media) * 60)
                fig_hist.add_vline(x=media_chegada, line_dash="dot", annotation_text=f"Média {h_media:02d}:{m_media:02d}")
            st.plotly_chart(fig_hist, use_container_width=True)

    # --- O ELSE FICOU AQUI NO FINAL (Correto) ---
    else:
        # Se df_bd estiver vazio (e não for só filtro), mostra mensagem
        if modo_demo:
            st.info("O modo demo está ativo, mas não gerou dados.")
        else:

            st.info("Insira dados na aba de Lançamento.")

