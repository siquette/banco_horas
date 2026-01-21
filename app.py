import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import date, time

# --- IMPORTAÇÕES MODULARES ---
import database as db
import utils as ut
from mock_data import gerar_dados_ficticios

# --- CONFIGURAÇÕES GERAIS ---
st.set_page_config(page_title="Gestão de Tempo Analytics", layout="wide", page_icon="📊")

# --- AUTENTICAÇÃO ---
def check_password():
    def password_entered():
        if st.session_state["password"] == st.secrets["geral"]["senha_acesso"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if st.session_state.get("password_correct", False):
        return True

    st.text_input("🔒 Digite a senha para acessar:", type="password", on_change=password_entered, key="password")
    if "password_correct" in st.session_state:
        st.error("😕 Senha incorreta")
    return False

if not check_password():
    st.stop()

# --- INICIALIZAÇÃO ---
try:
    db.init_db()
except Exception as e:
    st.error(f"Erro de conexão: {e}")

# --- SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Configurações")
    
    tipo_dados = st.radio(
        "Fonte de Dados:",
        (
            "📂 Banco Real (Neon)", 
            "🧪 Demo: Superávit (+)", 
            "🧪 Demo: Déficit (-)",
            "🔥 Demo: Feriado & FDS (Stress)"
        ),
        help="Escolha entre dados reais ou cenários simulados."
    )
    
    if "Demo" in tipo_dados:
        modo_demo = True
        st.warning(f"⚠️ Visualizando: {tipo_dados}")
        
        if "Superávit" in tipo_dados: cenario_escolhido = "superavit"
        elif "Déficit" in tipo_dados: cenario_escolhido = "deficit"
        else: cenario_escolhido = "teste_feriado"
        
        df_bd = gerar_dados_ficticios(cenario_escolhido)
        # Limpa cache apenas se mudar o cenário
        if "ultimo_cenario" not in st.session_state or st.session_state.ultimo_cenario != cenario_escolhido:
             st.cache_data.clear()
             st.session_state.ultimo_cenario = cenario_escolhido
    else:
        modo_demo = False
        try:
            df_bd = db.carregar_dados()
        except Exception as e:
            st.error(f"Erro ao carregar banco: {e}")
            df_bd = pd.DataFrame()

# --- INTERFACE ---
tab_lancamento, tab_analytics = st.tabs(["📝 Lançamento & Extrato", "📈 Análise Gerencial (BI)"])

# ABA 1: LANÇAMENTO (OTIMIZADA COM ST.FORM)
with tab_lancamento:
    st.title("Apontamento Diário")
    col_input, col_view = st.columns([1, 2])
    
    with col_input:
        with st.container(border=True):
            st.subheader("Novo Registro")
            
            # [PERFORMANCE UX] 
            # Tudo dentro do st.form não causa reload da página até clicar no botão Submit.
            with st.form(key="form_lancamento", clear_on_submit=False):
                
                # 1. Seleção de Data
                data_sel = st.date_input("Data do Registro", date.today())
                
                # Buscando dados prévios (apenas para preencher defaults se existirem)
                # Nota: Dentro do form, isso roda na renderização inicial, não a cada clique.
                rec = pd.DataFrame()
                if not df_bd.empty:
                    rec = df_bd[df_bd['data'] == str(data_sel)]

                # Defaults iniciais
                d_ent, d_sai = time(9,0), time(18,0)
                d_ai, d_av = time(12,0), time(13,0)
                d_ext_ini, d_ext_fim = time(0,0), time(0,0)
                d_feriado, d_home_office, d_obs = False, False, ""
                d_falta = False

                # Se já existe registro, carrega os valores
                if not rec.empty:
                    st.caption(f"✏️ Editando registro existente de: {data_sel.strftime('%d/%m/%Y')}")
                    d_obs = rec.iloc[0]['obs']
                    e_str, s_str = rec.iloc[0]['entrada'], rec.iloc[0]['saida']
                    
                    if e_str == "00:00:00" and s_str == "00:00:00": d_falta = True
                    
                    try: d_feriado = True if rec.iloc[0]['feriado_manual'] == 1 else False
                    except: pass
                    try: d_home_office = True if rec.iloc[0]['home_office'] == 1 else False
                    except: pass
                    
                    if not d_falta:
                        try:
                            h, m, s = map(int, rec.iloc[0]['extra_inicio'].split(':'))
                            d_ext_ini = time(h, m)
                            h, m, s = map(int, rec.iloc[0]['extra_fim'].split(':'))
                            d_ext_fim = time(h, m)
                        except: pass

                # Inputs Visuais
                ck1, ck2, ck3 = st.columns(3)
                is_feriado = ck1.checkbox("Feriado?", value=d_feriado, help="Zera a meta do dia")
                is_falta = ck2.checkbox("Falta?", value=d_falta, help="Considera 0h trabalhadas")
                is_home_office = ck3.checkbox("🏠 Home Office", value=d_home_office)

                c1, c2 = st.columns(2)
                entrada = c1.time_input("Entrada", d_ent)
                saida = c2.time_input("Saída", d_sai)
                
                c3, c4 = st.columns(2)
                almoco_ida = c3.time_input("Almoço Ida", d_ai)
                almoco_volta = c4.time_input("Almoço Volta", d_av)
                
                st.markdown("---")
                st.caption("Trabalho Extra")
                c5, c6 = st.columns(2)
                ext_ini = c5.time_input("Início Extra", d_ext_ini)
                ext_fim = c6.time_input("Fim Extra", d_ext_fim)
                
                obs = st.text_area("Observações", value=d_obs, height=68)
                
                # [BOTÃO DE AÇÃO]
                # O script só roda daqui para baixo quando isso for clicado
                submitted = st.form_submit_button("💾 Salvar Registro", type="primary", use_container_width=True, disabled=modo_demo)
                
                if submitted and not modo_demo:
                    # 1. Chama o Guardião de Integridade
                    dados_validos, msg_erro = ut.validar_registro(entrada, almoco_ida, almoco_volta, saida, is_falta)
                    
                    if not dados_validos:
                        st.error(msg_erro) # Mostra o erro vermelho berrante
                    else:
                        # 2. Se passou, prepara os dados
                        if is_falta:
                            entrada_salvar = time(0,0)
                            almoco_ida_salvar = time(0,0)
                            almoco_volta_salvar = time(0,0)
                            saida_salvar = time(0,0)
                            ext_ini_salvar = time(0,0)
                            ext_fim_salvar = time(0,0)
                        else:
                            entrada_salvar = entrada
                            almoco_ida_salvar = almoco_ida
                            almoco_volta_salvar = almoco_volta
                            saida_salvar = saida
                            ext_ini_salvar = ext_ini
                            ext_fim_salvar = ext_fim

                        # 3. Salva no Banco
                        db.salvar_registro(
                            str(data_sel), entrada_salvar, almoco_ida_salvar, almoco_volta_salvar, saida_salvar, 
                            ext_ini_salvar, ext_fim_salvar, obs, is_feriado, is_home_office
                        )
                        st.toast("✅ Registro salvo com sucesso!", icon="💾")
                        st.rerun()

            # Área de exclusão fora do form (pois é destrutiva e precisa de confirmação imediata)
            if not df_bd.empty and not modo_demo:
                 with st.expander("🗑️ Área de Perigo (Excluir)"):
                    lista_datas = df_bd['data'].sort_values(ascending=False).tolist()
                    dt_del = st.selectbox("Apagar dia:", options=lista_datas)
                    if st.button("Confirmar Exclusão", type="secondary", use_container_width=True):
                        db.excluir_registro(dt_del)
                        st.rerun()

with col_view:
        if not df_bd.empty:
            df = ut.processar_dataframe(df_bd)
            df[['meta', 'motivo']] = df.apply(ut.definir_meta, axis=1, result_type='expand')
            df['saldo'] = df['total_trabalhado'] - df['meta']
            
            # KPI Calculations
            saldo_total = df['saldo'].sum()
            dias_folga = saldo_total / 8.0
            
            credito_casa = df['extra_casa'].sum()
            credito_escritorio = df['extra_escritorio'].sum()
            total_creditos = credito_casa + credito_escritorio
            total_debitos = df[df['saldo'] < 0]['saldo'].sum()
            horas_premium = df[df['meta'] == 0]['total_trabalhado'].sum()
            
            media_dia = df[df['total_trabalhado'] > 0]['total_trabalhado'].mean()
            if pd.isna(media_dia): media_dia = 0.0

            # Layout Contábil
            st.markdown("### 🎯 Balanço de Horas")
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("💰 Saldo Líquido", f"{saldo_total:+.2f} h", delta_color="normal" if saldo_total >= 0 else "inverse")
            k2.metric("📈 Total Ganhos", f"+{total_creditos:.2f} h", delta_color="normal")
            k3.metric("📉 Total Débitos", f"{total_debitos:.2f} h", delta_color="inverse")
            k4.metric("🏖️ Dias de Folga", f"{dias_folga:+.1f} dias")
            
            st.markdown("---")
            st.markdown("### 📊 Detalhamento da Origem")
            d1, d2, d3, d4 = st.columns(4)
            d1.metric("🏠 Extra (Casa)", f"{credito_casa:.2f} h")
            d2.metric("🏢 Extra (Escritório)", f"{credito_escritorio:.2f} h")
            d3.metric("🔥 Plantões (FDS)", f"{horas_premium:.2f} h")
            d4.metric("⏱️ Média Diária", f"{media_dia:.2f} h")
            
            st.markdown("---")
            
            # Preparação Visual da Tabela
            df_display = df.copy()
            def icone_motivo(m):
                if "Domingo" in m or "Feriado" in m: return "🔴 " + m
                if "Sábado" in m: return "🟠 " + m
                return "🔵 " + m
            df_display['motivo_visual'] = df_display['motivo'].apply(icone_motivo)

            # [MELHORIA UX] Renomeando colunas para português amigável
            df_final = df_display[['data', 'horas_escritorio', 'horas_casa', 'total_trabalhado', 'saldo', 'motivo_visual', 'obs']].rename(columns={
                'data': 'Data',
                'horas_escritorio': 'Escritório',
                'horas_casa': 'Casa',
                'total_trabalhado': 'Total',
                'saldo': 'Saldo',
                'motivo_visual': 'Status',   # Renomeado de motivo_visual
                'obs': 'Observações'         # A nova coluna pedida!
            })

            st.dataframe(
                df_final.sort_values('Data', ascending=False)
                .style.format("{:.2f}", subset=['Escritório', 'Casa', 'Total', 'Saldo'])
                .background_gradient(subset=['Saldo'], cmap='RdYlGn', vmin=-8, vmax=8),
                use_container_width=True,
                hide_index=True # Esconde o índice numérico (0, 1, 2...) que não serve pra nada
            )
            st.download_button("📥 Excel", ut.to_excel(df), "ponto.xlsx")
        else:
            st.warning("Sem dados.")

# ABA 2: ANALYTICS (VERSÃO DEFINITIVA: FILTROS NOVOS + UX RICA RESTAURADA)
with tab_analytics:
    st.header("Análise Gerencial & BI")
    
    if not df_bd.empty:
        df = ut.processar_dataframe(df_bd)
        df[['meta', 'motivo']] = df.apply(ut.definir_meta, axis=1, result_type='expand')
        df['saldo'] = (df['total_trabalhado'] - df['meta']).round(2)
        
        # --- ÁREA DE FILTROS ---
        st.markdown("### 🔍 Filtros de Análise")
        
        # Filtro de Data
        min_date_bd, max_date_bd = df['data_dt'].min().date(), df['data_dt'].max().date()
        if "filtro_data" not in st.session_state: st.session_state.filtro_data = (min_date_bd, max_date_bd)
        def limpar_filtro(): 
            st.session_state.filtro_data = (min_date_bd, max_date_bd)
            st.session_state.filtro_fds = False 

        c_f1, c_f2, c_f3 = st.columns([2, 2, 1])
        
        range_sel = c_f1.date_input("Período", key="filtro_data")
        
        # [NOVO FILTRO] Checkbox Inteligente
        ver_apenas_fds = c_f2.checkbox("📅 Apenas Sáb / Dom / Feriados", key="filtro_fds", help="Filtra dias que não são úteis para ver o impacto na vida pessoal.")
        
        c_f3.write("") # Espaço de alinhamento
        if c_f3.button("🧹 Limpar Tudo", on_click=limpar_filtro): st.rerun()
        
        # APLICAÇÃO DOS FILTROS
        if isinstance(range_sel, tuple) and len(range_sel) == 2:
            mask_data = (df['data_dt'].dt.date >= range_sel[0]) & (df['data_dt'].dt.date <= range_sel[1])
            df_filtered = df.loc[mask_data].copy()
        else:
            df_filtered = df.copy()
            
        if ver_apenas_fds:
            # Filtra onde a META é 0 (Definição técnica de dia não útil)
            df_filtered = df_filtered[df_filtered['meta_calculada'] == 0]
            if df_filtered.empty:
                st.warning("Nenhum registro encontrado em Sábados, Domingos ou Feriados neste período.")

        st.markdown("---")

        if not df_filtered.empty:
            # 1. HEATMAP (GitHub Style)
            st.subheader("📅 Mapa de Intensidade")
            
            # [UX RESTAURADA] Explicação Rica
            with st.expander("ℹ️ Como ler este gráfico?"):
                st.markdown("""
                * **Conceito:** Cada quadradinho é um dia do ano.
                * **Cor Escura:** Dias de **Alto Trabalho** (Muitas horas).
                * **Cor Clara:** Dias de pouco trabalho.
                * **Espaços Vazios:** Dias sem registro (Faltas ou FDS).
                * **Objetivo:** Identificar visualmente épocas de *Burnout* (tudo escuro) ou *Ociosidade*.
                """)
                
            df_filtered['week'] = df_filtered['data_dt'].dt.isocalendar().week
            df_filtered['weekday_num'] = df_filtered['data_dt'].dt.weekday
            df_filtered['year'] = df_filtered['data_dt'].dt.year
            hm_data = df_filtered.groupby(['year', 'week', 'weekday_num'])['total_trabalhado'].sum().reset_index()
            
            fig_git = go.Figure(data=go.Heatmap(
                z=hm_data['total_trabalhado'], x=hm_data['week'], y=hm_data['weekday_num'],
                colorscale='Greens', xgap=3, ygap=3, hoverongaps=False,
                hovertemplate="Semana: %{x}<br>Dia: %{y}<br>Horas: %{z:.2f}h<extra></extra>"
            ))
            fig_git.update_layout(
                plot_bgcolor='rgba(0,0,0,0)', height=250,
                yaxis=dict(tickmode='array', tickvals=[0,1,2,3,4,5,6], ticktext=['Seg','Ter','Qua','Qui','Sex','Sáb','Dom'], autorange='reversed', title=None),
                xaxis=dict(showgrid=False, title="Semana do Ano"), margin=dict(l=40, r=40, t=20, b=20)
            )
            st.plotly_chart(fig_git, use_container_width=True)

            # 2. BARRAS COM MOTIVO
            st.subheader("📊 Histórico Detalhado")
            legendas = {'horas_escritorio': 'Escritório', 'horas_casa': 'Casa (HO+Extra)', 'data': 'Data', 'value': 'Horas', 'motivo_dia': 'Tipo de Dia'}
            
            fig_bar = px.bar(
                df_filtered.sort_values('data_dt'), x='data', y=['horas_escritorio', 'horas_casa'], 
                labels=legendas,
                color_discrete_map={'horas_escritorio': '#3498DB', 'horas_casa': '#E67E22'},
                hover_data=['motivo_dia'], 
                text_auto='.2f'
            )
            fig_bar.add_hline(y=ut.META_DIARIA, line_dash="dot", line_color="red", annotation_text="Meta 8h")
            fig_bar.update_layout(legend_title_text='') 
            fig_bar.update_traces(textposition="inside", cliponaxis=False)
            st.plotly_chart(fig_bar, use_container_width=True)

            # 3. SALDO E PIZZA
            c3, c4 = st.columns(2)
            with c3:
                st.subheader("📈 Saldo Acumulado")
                df_filtered = df_filtered.sort_values('data_dt')
                df_filtered['saldo_acumulado'] = df_filtered['saldo'].cumsum().round(2)
                
                fig_line = px.line(
                    df_filtered, x='data', y='saldo_acumulado', markers=True, 
                    labels={'saldo_acumulado': 'Saldo (h)', 'data': 'Data'}, line_shape="spline"
                )
                fig_line.add_hline(y=0, line_dash="dot", line_color="gray")
                fig_line.update_traces(hovertemplate='Data: %{x}<br>Saldo: %{y:.2f} h')
                st.plotly_chart(fig_line, use_container_width=True)
                
            with c4:
                st.subheader("🥧 Proporção Total")
                fig_pie = px.pie(
                    values=[df_filtered['horas_escritorio'].sum(), df_filtered['horas_casa'].sum()],
                    names=["Escritório", "Casa"], hole=0.4,
                    color_discrete_sequence=['#3498DB', '#E67E22']
                )
                fig_pie.update_traces(textinfo='percent+label', hovertemplate='%{label}: %{value:.2f} h')
                st.plotly_chart(fig_pie, use_container_width=True)

            st.markdown("---")
            
            # 4. SCATTER PLOT (COMPORTAMENTO)
            st.subheader("🧩 Padrão de Comportamento")
            
            # [UX RESTAURADA] Explicação Rica
            with st.expander("ℹ️ Entenda a correlação (Clique para expandir)"):
                c_ex1, c_ex2 = st.columns(2)
                with c_ex1:
                    st.markdown("""
                    **Eixos:**
                    * ↔️ **Horizontal (X):** Hora que você chegou.
                    * ↕️ **Vertical (Y):** Quantas horas trabalhou.
                    """)
                with c_ex2:
                    st.info("""
                    **💡 Como interpretar:**
                    * **Linha Reta:** Indica alta disciplina (você entrega 8h independente de que horas chega).
                    * **Linha Inclinada:** Indica rigidez (se chega tarde, trabalha menos).
                    """)

            def t_float(t):
                try: 
                    parts = list(map(int, str(t).split(':')))
                    return round(parts[0] + parts[1]/60, 2)
                except: return None
                
            df_filtered['ent_num'] = df_filtered['entrada'].apply(t_float)
            
            fig_scatter = px.scatter(
                df_filtered, x="ent_num", y="total_trabalhado", color="saldo",
                size="total_trabalhado", hover_data=['data', 'motivo_dia'], 
                color_continuous_scale="RdYlGn",
                labels={'ent_num': 'Chegada (h)', 'total_trabalhado': 'Jornada (h)', 'saldo': 'Saldo'}
            )
            fig_scatter.add_vline(x=9.0, line_dash="dot")
            fig_scatter.add_hline(y=ut.META_DIARIA, line_dash="dot")
            fig_scatter.update_traces(hovertemplate='Chegada: %{x:.2f}h<br>Jornada: %{y:.2f}h<br>Saldo: %{marker.color:.2f}h<br>Tipo: %{customdata[1]}')
            st.plotly_chart(fig_scatter, use_container_width=True)

            st.markdown("---")
            
            # 5. VIOLIN PLOT (UX COMPLETAMENTE RESTAURADA COM IMAGENS)
            st.subheader("🎻 Distribuição por Dia da Semana")
            
            with st.expander("ℹ️ Como ler este gráfico? (Guia Visual Completo)"):
                c_img, c_txt = st.columns([1, 2])
                
                with c_img:
                    st.caption("1. Anatomia")
                    st.image("https://miro.medium.com/v2/resize:fit:640/format:webp/1*cLRJpn99OZoOm1rrwf3X2Q.png", use_container_width=True)
                    st.markdown("---")
                    st.caption("2. Padrões de Rotina")
                    st.image("https://miro.medium.com/v2/resize:fit:640/format:webp/1*jqAm7rYF-ZqI27tm5B8XZA.png", caption="Fonte: Data Hackers", use_container_width=True)
                
                with c_txt:
                    st.markdown("""
                    ### 🧠 Decifrando sua Rotina
                    
                    **1. Onde está a "Barriga"? (Moda)**
                    Olhe para a parte mais larga do violino. É ali que sua rotina acontece.
                    * **No 8h:** Rotina saudável e consistente.
                    * **No 10h:** Tendência a horas extras.
                    
                    **2. Quantas "Barrigas"? (Veja imagem 2)**
                    * **Uma só (Normal):** Você tem um padrão único.
                    * **Duas (Bimodal):** Você tem "duas personalidades" (ex: dias que sai cedo vs dias que vira a noite).
                    
                    **3. Extremos (Fios Finos)**
                    Mostram seus recordes de horário mínimo e máximo daquele dia da semana.
                    """)

            df_filtered['weekday_name'] = df_filtered['data_dt'].dt.strftime("%A")
            dias_map = {'Monday': 'Segunda', 'Tuesday': 'Terça', 'Wednesday': 'Quarta', 'Thursday': 'Quinta', 'Friday': 'Sexta', 'Saturday': 'Sábado', 'Sunday': 'Domingo'}
            df_filtered['dia_pt'] = df_filtered['weekday_name'].map(dias_map).fillna(df_filtered['weekday_name'])
            ordem = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
            
            fig_violin = px.violin(
                df_filtered, y="total_trabalhado", x="dia_pt", box=True, points="all", 
                hover_data=['data'], color="dia_pt", category_orders={"dia_pt": ordem},
                labels={'dia_pt': 'Dia', 'total_trabalhado': 'Horas'}
            )
            fig_violin.add_hline(y=ut.META_DIARIA, line_dash="dot", line_color="red")
            fig_violin.update_layout(showlegend=False)
            fig_violin.update_traces(hovertemplate='Dia: %{x}<br>Horas: %{y:.2f} h')
            st.plotly_chart(fig_violin, use_container_width=True)
            
            # 6. HISTOGRAMA
            st.markdown("---")
            st.subheader("⏰ Consistência de Chegada")
            
            # [UX RESTAURADA] Dica Rápida
            with st.expander("ℹ️ Dica de Pontualidade"):
                 st.markdown("Barras altas e finas indicam **disciplina**. Barras baixas e espalhadas indicam **horários flexíveis/caóticos**.")

            fig_hist = px.histogram(
                df_filtered, x="ent_num", nbins=20, 
                labels={'ent_num': 'Hora Chegada', 'count': 'Freq.'},
                color_discrete_sequence=['#9B59B6']
            )
            fig_hist.update_layout(bargap=0.1)
            fig_hist.update_traces(hovertemplate='Hora: %{x:.2f}h<br>Dias: %{y}')
            st.plotly_chart(fig_hist, use_container_width=True)

    else:
        if modo_demo:
            st.info("⚠️ Sem dados no cenário selecionado.")
        else:
            st.info("👋 Insira dados na aba Lançamento.")