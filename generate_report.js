"use strict";
const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, VerticalAlign, PageNumber, PageBreak, TableOfContents,
  LevelFormat, ExternalHyperlink, ImageRun,
} = require("docx");

// ── Constantes de página A4 (DXA: 1440 = 1 polegada) ────────────────────────
const PAGE_W   = 11906;
const PAGE_H   = 16838;
const MARGIN   = 1440;           // 1 pol. nas 4 bordas
const CONTENT  = PAGE_W - 2 * MARGIN;  // 9026 DXA

// ── Paleta ───────────────────────────────────────────────────────────────────
const NAVY   = "1F3864";
const GRAY_H = "D9E2F3";  // cabeçalho de tabela
const GRAY_L = "F2F2F2";  // linha alternada
const WHITE  = "FFFFFF";
const BLACK  = "000000";

// ── Data atual ───────────────────────────────────────────────────────────────
const hoje = new Date();
const dataFormatada = hoje.toLocaleDateString("pt-BR", {
  day: "2-digit", month: "long", year: "numeric",
});

// ─────────────────────────────────────────────────────────────────────────────
// Helpers de parágrafo e célula
// ─────────────────────────────────────────────────────────────────────────────
function p(text, opts = {}) {
  const {
    bold = false, color = BLACK, size = 24, font = "Arial",
    align = AlignmentType.LEFT, spaceBefore = 0, spaceAfter = 80,
    italic = false, heading = null,
  } = opts;
  const run = new TextRun({ text, bold, color, size, font, italics: italic });
  const paraOpts = {
    children: [run],
    alignment: align,
    spacing: { before: spaceBefore, after: spaceAfter },
  };
  if (heading) paraOpts.heading = heading;
  return new Paragraph(paraOpts);
}

function pRuns(runs, opts = {}) {
  const {
    align = AlignmentType.LEFT, spaceBefore = 0, spaceAfter = 80,
    heading = null,
  } = opts;
  const paraOpts = {
    children: runs,
    alignment: align,
    spacing: { before: spaceBefore, after: spaceAfter },
  };
  if (heading) paraOpts.heading = heading;
  return new Paragraph(paraOpts);
}

function bullet(text, opts = {}) {
  return new Paragraph({
    numbering: { reference: "bullets", level: 0 },
    children: [new TextRun({ text, font: "Arial", size: 24, ...opts })],
    spacing: { after: 40 },
  });
}

function blankLine() {
  return new Paragraph({ children: [new TextRun("")], spacing: { after: 0 } });
}

const THIN = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const BORDERS = { top: THIN, bottom: THIN, left: THIN, right: THIN };

function cell(text, opts = {}) {
  const {
    shade = WHITE, bold = false, size = 22, color = BLACK,
    width = null, vAlign = VerticalAlign.CENTER, italic = false,
    colspan = 1,
  } = opts;
  const cellOpts = {
    borders: BORDERS,
    shading: { fill: shade, type: ShadingType.CLEAR },
    margins: { top: 80, bottom: 80, left: 120, right: 120 },
    verticalAlign: vAlign,
    children: [new Paragraph({
      alignment: AlignmentType.LEFT,
      children: [new TextRun({ text, bold, size, color, font: "Arial", italics: italic })],
    })],
  };
  if (width)   cellOpts.width = { size: width, type: WidthType.DXA };
  if (colspan > 1) cellOpts.columnSpan = colspan;
  return new TableCell(cellOpts);
}

function headerRow(cols, widths) {
  return new TableRow({
    tableHeader: true,
    children: cols.map((txt, i) =>
      cell(txt, { shade: GRAY_H, bold: true, size: 22, width: widths[i] })
    ),
  });
}

function dataRow(cols, widths, shade = WHITE) {
  return new TableRow({
    children: cols.map((txt, i) =>
      cell(txt, { shade, width: widths[i] })
    ),
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Seção 1 — Visão Geral
// ─────────────────────────────────────────────────────────────────────────────
const sec1 = [
  p("1. Visao Geral do Sistema", { heading: HeadingLevel.HEADING_1 }),

  p("Objetivo", { heading: HeadingLevel.HEADING_2 }),
  p("O Clima Solar e um aplicativo de monitoramento de clima espacial por geolocalizacao. Dado um par de coordenadas (latitude/longitude), o sistema consulta indices ionosfericos e geomagneticos em tempo real e retorna um score de risco composto (0-1) classificado em tres niveis: BAIXO (verde), MEDIO (amarelo) e ALTO (vermelho). O publico-alvo sao operadores de sistemas GNSS (GPS, GLONASS, Galileo, BeiDou) que precisam de alertas antecipados sobre condicoes adversas de propagacao de sinal.", { spaceAfter: 120 }),

  p("Arquitetura Geral", { heading: HeadingLevel.HEADING_2 }),
  p("O sistema e composto por tres camadas principais:", { spaceAfter: 60 }),
  bullet("Frontend (PWA): Interface HTML/CSS/JavaScript pura, sem frameworks, hospedada no GitHub Pages. Consome a API REST do backend via fetch() e armazena historico local em localStorage."),
  bullet("Backend (API REST): Servidor FastAPI (Python 3.12) hospedado na plataforma Railway. Executa um loop de atualizacao a cada 15 minutos buscando indices de fontes externas e disponibiliza endpoints REST com cache em memoria."),
  bullet("Fontes de Dados: NOAA SWPC (Kp, F10.7), Kyoto WDC via espelho NOAA (Dst) e EMBRACE/INPE (S4, sigma_phi). Os dados sao buscados automaticamente pelo backend; o frontend nunca acessa as fontes diretamente."),
  blankLine(),

  p("Tecnologias Utilizadas", { heading: HeadingLevel.HEADING_2 }),
  (() => {
    const widths = [2800, 6226];
    return new Table({
      width: { size: CONTENT, type: WidthType.DXA },
      columnWidths: widths,
      rows: [
        headerRow(["Camada", "Tecnologia / Ferramenta"], widths),
        dataRow(["Backend", "Python 3.12 | FastAPI | httpx | NumPy | SQLite | Railway (Docker)"], widths, WHITE),
        dataRow(["Frontend", "HTML5 / CSS3 / JavaScript puro | Leaflet 1.9.4 | Chart.js 4.4.1 | PWA (Service Worker)"], widths, GRAY_L),
        dataRow(["Hospedagem", "Backend: Railway (container Docker) | Frontend: GitHub Pages via GitHub Actions"], widths, WHITE),
        dataRow(["Repositorio", "https://github.com/ThomasAK47/clima-solar"], widths, GRAY_L),
      ],
    });
  })(),
  blankLine(),
];

// ─────────────────────────────────────────────────────────────────────────────
// Seção 2 — Fontes de Dados
// ─────────────────────────────────────────────────────────────────────────────
const sec2 = [
  new Paragraph({ children: [new PageBreak()] }),
  p("2. Fontes de Dados", { heading: HeadingLevel.HEADING_1 }),
  p("O backend realiza requisicoes HTTP a tres fontes publicas a cada 15 minutos. Todas as fontes sao de acesso publico e nao requerem autenticacao.", { spaceAfter: 120 }),

  // 2.1 NOAA Kp
  p("2.1 NOAA SWPC — Indice Kp", { heading: HeadingLevel.HEADING_2 }),
  p("Nome e Instituicao: NOAA Space Weather Prediction Center (Boulder, CO, EUA).", { spaceAfter: 60 }),
  bullet("URL: https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"),
  bullet("Parametro coletado: Indice Kp planetario (campo \"Kp\" com K maiusculo), frequencia de 15 minutos."),
  bullet("Metodo de autenticacao: nenhum (endpoint publico REST/JSON)."),
  bullet("Formato de resposta: lista de objetos JSON com campos time_tag e Kp. O coletor suporta tanto o formato lista-de-dicts quanto lista-de-listas para garantir compatibilidade com variacoes do endpoint."),
  bullet("Tratamento de falhas: em caso de erro HTTP ou ausencia de valores validos (campo Kp nulo), o ciclo de coleta registra o erro em snapshot.errors e mantem o ultimo valor disponivel em cache ate o proximo ciclo bem-sucedido."),
  blankLine(),

  // 2.2 NOAA F10.7
  p("2.2 NOAA SWPC — Fluxo Solar F10.7", { heading: HeadingLevel.HEADING_2 }),
  p("Nome e Instituicao: NOAA Space Weather Prediction Center.", { spaceAfter: 60 }),
  bullet("URL: https://services.swpc.noaa.gov/products/summary/10cm-flux.json"),
  bullet("Parametro coletado: Fluxo de radio solar em 10,7 cm (campo \"flux\" ou \"Flux\"), medido em unidades de fluxo solar (sfu)."),
  bullet("Metodo de autenticacao: nenhum. Timeout de 15 segundos."),
  bullet("Formato de resposta: dicionario JSON ou lista de dicts/listas. O coletor identifica automaticamente o formato pelo tipo do dado retornado e extrai o campo de fluxo pelo nome ou posicao."),
  bullet("Tratamento de falhas: idem ao Kp — excecao capturada, erro registrado, ciclo continua com os demais parametros."),
  blankLine(),

  // 2.3 Dst
  p("2.3 Kyoto WDC / NOAA — Indice Dst", { heading: HeadingLevel.HEADING_2 }),
  p("Nome e Instituicao: Kyoto World Data Center for Geomagnetism (Kyoto, Japao), espelho disponibilizado pela NOAA.", { spaceAfter: 60 }),
  bullet("URL: https://services.swpc.noaa.gov/products/kyoto-dst.json"),
  bullet("Parametro coletado: Indice de perturbacao geomagnetica Dst (campo \"dst\"), em nanoTesla (nT). Valores negativos indicam tempestades geomagneticas."),
  bullet("Metodo de autenticacao: nenhum (espelho publico NOAA). Timeout de 15 segundos."),
  bullet("Formato de resposta: lista de dicts {time_tag, dst}. O coletor filtra entradas com dst == null antes de selecionar o valor mais recente."),
  bullet("Tratamento de falhas: excecao capturada por try/except, erro registrado em snapshot.errors. O motor de risco usa fallback de sub-score 0.5 para Dst quando o dado esta indisponivel."),
  blankLine(),

  // 2.4 EMBRACE
  p("2.4 EMBRACE/INPE — S4 e Sigma_phi", { heading: HeadingLevel.HEADING_2 }),
  p("Nome e Instituicao: EMBRACE (Estudo e Monitoramento Brasileiro do Clima Espacial), programa do INPE (Instituto Nacional de Pesquisas Espaciais, Sao Jose dos Campos, SP).", { spaceAfter: 60 }),
  bullet("URL base: https://embracedata.inpe.br/scintillation/maps/"),
  bullet("Parametros coletados: S4 (indice de cintilacao de amplitude, adimensional) e sigma_phi (desvio padrao de fase, em radianos). Os produtos ROTI e VTEC nao estao disponiveis neste produto de mapa."),
  bullet("Estrutura de acesso: listagem Apache de diretorios organizados por parametro / ano / dia-do-ano (DOY). O coletor le o HTML da listagem, extrai os nomes dos arquivos via regex (padroes S4_MAP_*.txt e SIGMAPHI_MAP_*.txt) e seleciona o mais recente por hora/minuto."),
  bullet("Formato das matrizes: arquivo ASCII delimitado por ponto-e-virgula (;), 191 colunas (lon -140 a -20 graus) e ~128 linhas (lat -60 a +50 graus). Valores -1 indicam ausencia de dado e sao convertidos para NaN."),
  bullet("Interpolacao: bilinear sobre a grade lat/lon, calculada em CPU puro (NumPy) sem chamadas de rede. Cada requisicao /status interpola as matrizes ja armazenadas em cache de memoria."),
  bullet("Defasagem de processamento: ~6 a 9 horas (normal para o produto EMBRACE). Nao ha atraso artificial — e o tempo de processamento do proprio instituto."),
  bullet("Metodo de autenticacao: nenhum (servidor publico Apache). Timeout de 20 segundos por requisicao."),
  bullet("Tratamento de falhas: se o diretorio do dia atual nao tiver arquivos, o coletor tenta o dia anterior. Se ambos falharem, as matrizes ficam como None e o motor de risco redistribui os pesos de S4 e sigma_phi para os demais parametros disponiveis."),
  blankLine(),
];

// ─────────────────────────────────────────────────────────────────────────────
// Seção 3 — Parâmetros
// ─────────────────────────────────────────────────────────────────────────────
const colW3 = [1100, 2200, 800, 1200, 1400, 1400, 926]; // soma = 9026
const sec3 = [
  new Paragraph({ children: [new PageBreak()] }),
  p("3. Parametros Monitorados", { heading: HeadingLevel.HEADING_1 }),
  p("A tabela a seguir descreve cada parametro coletado, sua grandeza fisica, unidade de medida, os tres limiares de risco e a fonte de dados correspondente.", { spaceAfter: 120 }),
  new Table({
    width: { size: CONTENT, type: WidthType.DXA },
    columnWidths: colW3,
    rows: [
      headerRow(
        ["Parametro", "Descricao fisica", "Unidade", "Baixo (verde)", "Medio (amarelo)", "Alto (vermelho)", "Fonte"],
        colW3
      ),
      dataRow(["Kp", "Indice geomagnetico planetario — mede perturbacoes no campo magnetico terrestre causadas pelo vento solar", "0 a 9", "0 a 3", "4 a 5", ">= 6", "NOAA SWPC"], colW3, WHITE),
      dataRow(["Dst", "Indice de tempestade geomagnetica — variacao do campo magnetico no equador; valores negativos indicam tempestades", "nT", "> -30", "-30 a -100", "< -100", "Kyoto/NOAA"], colW3, GRAY_L),
      dataRow(["F10.7", "Fluxo de radio solar em 10,7 cm — proxy da atividade solar e ionizacao ionosferica", "sfu", "< 100", "100 a 150", "> 150", "NOAA SWPC"], colW3, WHITE),
      dataRow(["S4", "Indice de cintilacao de amplitude — variacao da potencia do sinal GNSS ao atravessar a ionosfera", "admens.", "< 0,3", "0,3 a 0,6", "> 0,6", "EMBRACE/INPE"], colW3, GRAY_L),
      dataRow(["sigma_phi", "Cintilacao de fase — desvio padrao da fase do sinal GNSS em 60 s (Phi60); afeta diretamente o rastreamento de portadora nos receptores", "rad", "< 0,1", "0,1 a 0,5", "> 0,5", "EMBRACE/INPE"], colW3, WHITE),
    ],
  }),
  blankLine(),
  p("Nota: ROTI (Rate of TEC Index) e VTEC (Vertical Total Electron Content) estao previstos no modelo de dados (campos roti_tecu_min e vtec_tecu no modelo EmbraceData) mas nao sao fornecidos pelo produto de mapa EMBRACE atualmente utilizado. Quando presentes, teriam pesos de 0,10 cada.", { italic: true, size: 22, color: "555555" }),
  blankLine(),
];

// ─────────────────────────────────────────────────────────────────────────────
// Seção 4 — Motor de Risco
// ─────────────────────────────────────────────────────────────────────────────
const sec4 = [
  new Paragraph({ children: [new PageBreak()] }),
  p("4. Motor de Risco — Calculo do Score Composto", { heading: HeadingLevel.HEADING_1 }),

  p("4.1 Normalizacao por Parametro", { heading: HeadingLevel.HEADING_2 }),
  p("Cada parametro bruto e convertido em um sub-score normalizado no intervalo [0, 1] por funcoes de normalizacao lineares por partes. As funcoes seguem um padrao de tres zonas: zona verde (sub-score 0 a 0,3), zona amarela (0,3 a 0,6) e zona vermelha (0,6 a 1,0). A seguir estao as funcoes implementadas em risk_engine.py:", { spaceAfter: 80 }),

  bullet("Kp: [0, 4) -> linear de 0 a 0,3; [4, 6) -> linear de 0,3 a 0,6; [6, 9] -> linear de 0,6 a 1,0."),
  bullet("Dst: (> -30) -> 0,0; [-100, -30] -> linear de 0,3 a 0,6; (< -100) -> linear de 0,6 a 1,0."),
  bullet("F10.7: [0, 100) -> linear de 0 a 0,3; [100, 150) -> linear de 0,3 a 0,6; [>= 150] -> linear de 0,6 a 1,0."),
  bullet("S4: [0, 0,3) -> linear de 0 a 0,3; [0,3, 0,6) -> linear de 0,3 a 0,6; [>= 0,6] -> linear de 0,6 a 1,0."),
  bullet("sigma_phi: [0, 0,1) -> linear de 0 a 0,3; [0,1, 0,5) -> linear de 0,3 a 0,6; [>= 0,5] -> linear de 0,6 a 1,0."),
  blankLine(),

  p("4.2 Pesos e Score Composto", { heading: HeadingLevel.HEADING_2 }),
  p("O score composto e calculado como uma media ponderada dos sub-scores dos parametros disponiveis:", { spaceAfter: 60 }),
  (() => {
    const widths = [3000, 2013, 4013];
    return new Table({
      width: { size: CONTENT, type: WidthType.DXA },
      columnWidths: widths,
      rows: [
        headerRow(["Parametro", "Peso nominal", "Descricao"], widths),
        dataRow(["Kp",        "0,20 (20%)", "Atividade geomagnetica global"], widths, WHITE),
        dataRow(["Dst",       "0,15 (15%)", "Intensidade de tempestade geomagnetica"], widths, GRAY_L),
        dataRow(["F10.7",     "0,15 (15%)", "Atividade solar / ionizacao ionosferica"], widths, WHITE),
        dataRow(["S4",        "0,175 (17,5%)", "Cintilacao de amplitude GNSS"], widths, GRAY_L),
        dataRow(["sigma_phi", "0,125 (12,5%)", "Cintilacao de fase GNSS"], widths, WHITE),
        dataRow(["ROTI",      "0,10 (10%)",  "Taxa de variacao de TEC (futuro)"], widths, GRAY_L),
        dataRow(["VTEC",      "0,10 (10%)",  "Conteudo eletronico total vertical (futuro)"], widths, WHITE),
        dataRow(["TOTAL",     "1,00 (100%)", ""], widths, GRAY_H),
      ],
    });
  })(),
  blankLine(),
  p("Formula do score final:", { bold: true, spaceAfter: 40 }),
  pRuns([
    new TextRun({ text: "score = SUM(sub_score[i] * peso[i]) / SUM(peso[i])", font: "Courier New", size: 22, bold: true }),
  ], { spaceBefore: 40, spaceAfter: 80 }),
  p("onde o somatorio considera apenas os parametros com valores disponiveis. Parametros ausentes (null) nao sao incluidos no numerador nem no denominador, garantindo que o score sempre utilize 100% do peso disponivel — nunca 80% ou menos.", { spaceAfter: 120 }),

  p("4.3 Comportamento com Fontes Indisponiveis", { heading: HeadingLevel.HEADING_2 }),
  p("O motor distingue dois tipos de fallback:", { spaceAfter: 60 }),
  bullet("Kp, Dst e F10.7: se indisponiveis, recebem sub-score de fallback igual a 0,5 (risco moderado) e sao incluidos no calculo com seu peso integral. Isso evita que falhas de rede na NOAA mascararem um evento solar."),
  bullet("S4, sigma_phi, ROTI e VTEC: se indisponiveis (None), seus pesos sao removidos tanto do numerador quanto do denominador. O score e calculado apenas sobre os parametros presentes, com redistribuicao proporcional automatica dos pesos."),
  blankLine(),

  p("4.4 Classificacao de Risco", { heading: HeadingLevel.HEADING_2 }),
  (() => {
    const widths = [2200, 2200, 2200, 2426];
    return new Table({
      width: { size: CONTENT, type: WidthType.DXA },
      columnWidths: widths,
      rows: [
        headerRow(["Nivel", "Score", "Cor", "Interpretacao"], widths),
        dataRow(["BAIXO",  "< 0,3",       "Verde",    "Condicoes normais. Operacoes GNSS sem restricoes."], widths, WHITE),
        dataRow(["MEDIO",  "0,3 a 0,6",   "Amarelo",  "Perturbacoes moderadas. Monitoramento recomendado."], widths, GRAY_L),
        dataRow(["ALTO",   ">= 0,6",      "Vermelho", "Condicoes adversas. Degradacao de performance GNSS provavel."], widths, WHITE),
      ],
    });
  })(),
  blankLine(),
];

// ─────────────────────────────────────────────────────────────────────────────
// Seção 5 — API REST
// ─────────────────────────────────────────────────────────────────────────────
const colW5 = [900, 2000, 2500, 2426, 1200]; // soma = 9026
const sec5 = [
  new Paragraph({ children: [new PageBreak()] }),
  p("5. API REST", { heading: HeadingLevel.HEADING_1 }),
  p("O backend expoe quatro endpoints publicos. A URL base de producao e https://clima-solar-production.up.railway.app. CORS esta habilitado para todas as origens (allow_origins=[\"*\"]).", { spaceAfter: 120 }),
  new Table({
    width: { size: CONTENT, type: WidthType.DXA },
    columnWidths: colW5,
    rows: [
      headerRow(["Metodo", "Rota", "Parametros", "Resposta", "Cache"], colW5),
      dataRow(["GET", "/status", "lat (float), lon (float) — obrigatorios; -90<=lat<=90, -180<=lon<=180", "score composto, nivel de risco, sub-scores por parametro, valores brutos, idade dos dados, erros de coleta", "Nenhum (tempo real)"], colW5, WHITE),
      dataRow(["GET", "/history", "lat (float), lon (float) — obrigatorios", "Ultimas 48 horas de leituras para a localizacao (arredondada a 1 decimal de grau), ordenadas da mais antiga para a mais recente", "Nenhum"], colW5, GRAY_L),
      dataRow(["GET", "/heatmap", "Nenhum", "Grade 1,0 grau cobrindo o Brasil (~900 pontos) com {lat, lon, s4, score} por ponto", "15 minutos (servidor)"], colW5, WHITE),
      dataRow(["GET", "/health", "Nenhum", "{ok: true, cache_age_s, embrace_loaded} — utilizado pelo Railway para healthcheck", "Nenhum"], colW5, GRAY_L),
    ],
  }),
  blankLine(),
  p("Persistencia de historico: a cada requisicao /status bem-sucedida, o sistema persiste o snapshot (score, Kp, Dst, F10.7, S4, sigma_phi) em banco SQLite localizado em /data/historia.db (volume Railway montado). A retencao e de 48 horas, com deduplicacao automatica (ignora escrita se ja existe registro para a mesma localizacao nos ultimos 10 minutos). Operacoes de banco sao executadas em asyncio.to_thread() para nao bloquear o event loop.", { spaceAfter: 80 }),
  blankLine(),
];

// ─────────────────────────────────────────────────────────────────────────────
// Seção 6 — Frontend
// ─────────────────────────────────────────────────────────────────────────────
const sec6 = [
  new Paragraph({ children: [new PageBreak()] }),
  p("6. Frontend e Interface", { heading: HeadingLevel.HEADING_1 }),

  p("6.1 Tecnologia", { heading: HeadingLevel.HEADING_2 }),
  p("O frontend e um Progressive Web App (PWA) implementado em HTML5, CSS3 e JavaScript puro (sem frameworks). Um unico arquivo index.html contem toda a logica da aplicacao. O deploy e feito automaticamente via GitHub Actions para o GitHub Pages a cada push no branch main.", { spaceAfter: 120 }),

  p("6.2 Funcionalidades Principais", { heading: HeadingLevel.HEADING_2 }),
  bullet("Geolocalizacao: usa a API Geolocation do browser para obter lat/lon do usuario e consultar o /status do backend."),
  bullet("Score card principal: exibe nivel de risco (BAIXO/MEDIO/ALTO) com cor e barra de progresso animada."),
  bullet("Cards de indices: 5 cards (Kp, Dst, S4, F10.7, sigma_phi) com barras coloridas indicando a zona de risco e badges de tendencia (seta para cima, baixo ou lateral) calculados comparando a ultima e a penultima leitura armazenadas em localStorage."),
  bullet("Mapa ionosferico: mapa Leaflet com tiles CartoDB Dark + marcador da localizacao do usuario. Heatmap de retangulos 1,5 x 1,5 graus coloridos por score (ativavel via toggle), alimentado pelo endpoint /heatmap."),
  bullet("Grafico historico: Chart.js 4.4.1, multiplas series (Score, Kp, Dst, S4, sigma_phi), buscando dados do /history com fallback para localStorage."),
  bullet("Notificacoes push nativas: alerta quando o nivel de risco muda de categoria (ex.: BAIXO -> MEDIO)."),
  bullet("PWA completo: manifest.json, service worker (cache-first para assets estaticos, network-first para chamadas de API), icones 192px e 512px, instalavel como app nativo."),
  bullet("Modo offline: banner amarelo informativo quando o dispositivo esta sem conectividade."),
  bullet("Auto-refresh: a interface atualiza automaticamente a cada 5 minutos."),
  blankLine(),

  p("6.3 Hospedagem", { heading: HeadingLevel.HEADING_2 }),
  p("O frontend e servido estaticamente pelo GitHub Pages. O workflow .github/workflows/deploy-pages.yml copia o conteudo de frontend/ para o ambiente de Pages a cada push. URL de producao: https://thomasak47.github.io/clima-solar/", { spaceAfter: 80 }),
  blankLine(),
];

// ─────────────────────────────────────────────────────────────────────────────
// Seção 7 — Limitações
// ─────────────────────────────────────────────────────────────────────────────
const sec7 = [
  new Paragraph({ children: [new PageBreak()] }),
  p("7. Limitacoes e Trabalhos Futuros", { heading: HeadingLevel.HEADING_1 }),

  p("7.1 Limitacoes Atuais", { heading: HeadingLevel.HEADING_2 }),
  bullet("Defasagem dos dados EMBRACE: o produto de mapa EMBRACE/INPE tem defasagem tipica de 6 a 9 horas em relacao ao tempo real. Isso e intrinseco ao fluxo de processamento do instituto e nao pode ser reduzido pelo aplicativo."),
  bullet("Cobertura geografica limitada: a grade EMBRACE cobre longitude -140 a -20 graus e latitude -60 a +50 graus (Americas). Requisicoes de pontos fora dessa janela retornam S4 e sigma_phi como null, com redistribuicao de pesos."),
  bullet("ROTI e VTEC ausentes: o produto de mapa utilizado nao inclui ROTI (Rate of TEC Index) nem VTEC (Vertical TEC). Os campos estao modelados no codigo mas sempre retornam null na versao atual."),
  bullet("Historico por localizacao, sem autenticacao de usuario: o historico SQLite e indexado por coordenadas arredondadas a 1 decimal de grau (~11 km de precisao). Nao ha identificacao de dispositivo ou usuario, o que pode causar colisoes em areas de alta densidade."),
  bullet("Banco de dados SQLite: adequado para o volume atual (um registro por localizacao a cada 10 min, retencao de 48h). Para escalas maiores, seria necessaria migracao para PostgreSQL."),
  blankLine(),

  p("7.2 Trabalhos Futuros", { heading: HeadingLevel.HEADING_2 }),
  bullet("VTEC via IONEX: integracao com arquivos .INX do IGS/NASA CDDIS (https://cddis.nasa.gov/archive/gnss/products/ionex/), com defasagem de ~2 horas e acesso publico mediante registro gratuito."),
  bullet("Migracao para PostgreSQL no Railway: necessaria se a base de usuarios crescer significativamente, aproveitando o suporte nativo do Railway a PostgreSQL."),
  bullet("Historico por dispositivo: implementar identificacao de sessao (token anonimo ou autenticacao leve) para separar historicos de diferentes usuarios na mesma regiao."),
  bullet("Resolucao do heatmap ajustavel: o endpoint /heatmap atualmente usa passo de 1,0 grau (~2025 pontos). Poderia ser parametrizavel para oferecer resolucoes mais altas (~0,5 grau) ou mais baixas conforme a performance do cliente."),
  bullet("Alertas por email/webhook: alem das notificacoes push do browser, enviar alertas proativos por email ou webhook (ex.: Slack) quando o score superar limiares configurados pelo usuario."),
  blankLine(),
];

// ─────────────────────────────────────────────────────────────────────────────
// Seção 8 — Referências
// ─────────────────────────────────────────────────────────────────────────────
const sec8 = [
  p("8. Referencias", { heading: HeadingLevel.HEADING_1 }),

  p("[1] NOAA Space Weather Prediction Center (SWPC). Real-Time Solar Wind and Geomagnetic Indices.", { spaceAfter: 40 }),
  pRuns([
    new TextRun({ text: "     Disponivel em: ", font: "Arial", size: 24 }),
    new ExternalHyperlink({
      children: [new TextRun({ text: "https://www.swpc.noaa.gov", style: "Hyperlink", font: "Arial", size: 24 })],
      link: "https://www.swpc.noaa.gov",
    }),
  ], { spaceAfter: 100 }),

  p("[2] Kyoto World Data Center for Geomagnetism. Dst Index. Kyoto University, Japao.", { spaceAfter: 40 }),
  pRuns([
    new TextRun({ text: "     Disponivel em: ", font: "Arial", size: 24 }),
    new ExternalHyperlink({
      children: [new TextRun({ text: "http://wdc.kugi.kyoto-u.ac.jp/dstdir/", style: "Hyperlink", font: "Arial", size: 24 })],
      link: "http://wdc.kugi.kyoto-u.ac.jp/dstdir/",
    }),
  ], { spaceAfter: 100 }),

  p("[3] EMBRACE — Estudo e Monitoramento Brasileiro do Clima Espacial. INPE, Sao Jose dos Campos, Brasil.", { spaceAfter: 40 }),
  pRuns([
    new TextRun({ text: "     Disponivel em: ", font: "Arial", size: 24 }),
    new ExternalHyperlink({
      children: [new TextRun({ text: "http://www2.inpe.br/climaespacial/portal/embrace/", style: "Hyperlink", font: "Arial", size: 24 })],
      link: "http://www2.inpe.br/climaespacial/portal/embrace/",
    }),
  ], { spaceAfter: 100 }),

  p("[4] IGS — International GNSS Service. IONEX Products (VTEC). NASA CDDIS Archive.", { spaceAfter: 40 }),
  pRuns([
    new TextRun({ text: "     Disponivel em: ", font: "Arial", size: 24 }),
    new ExternalHyperlink({
      children: [new TextRun({ text: "https://cddis.nasa.gov/archive/gnss/products/ionex/", style: "Hyperlink", font: "Arial", size: 24 })],
      link: "https://cddis.nasa.gov/archive/gnss/products/ionex/",
    }),
  ], { spaceAfter: 100 }),

  p("[5] FastAPI. Tiangolo. Disponivel em: https://fastapi.tiangolo.com", { spaceAfter: 40 }),
  p("[6] Leaflet.js — an open-source JavaScript library for mobile-friendly interactive maps (v1.9.4). Disponivel em: https://leafletjs.com", { spaceAfter: 40 }),
  p("[7] Chart.js — Simple yet flexible JavaScript charting library (v4.4.1). Disponivel em: https://www.chartjs.org", { spaceAfter: 40 }),
];

// ─────────────────────────────────────────────────────────────────────────────
// CAPA
// ─────────────────────────────────────────────────────────────────────────────
const capa = [
  // Espaco superior
  ...Array(8).fill(null).map(() => blankLine()),
  p("Clima Solar", {
    bold: true, size: 64, color: NAVY,
    align: AlignmentType.CENTER, spaceAfter: 80,
  }),
  p("Documentacao Tecnica", {
    bold: true, size: 36, color: NAVY,
    align: AlignmentType.CENTER, spaceAfter: 40,
  }),
  p("Sistema de Monitoramento de Clima Espacial para Operacoes GNSS", {
    size: 28, color: "444444",
    align: AlignmentType.CENTER, spaceAfter: 0,
  }),
  // Linha decorativa
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 240, after: 240 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: NAVY, space: 1 } },
    children: [new TextRun("")],
  }),
  p(`Versao 1.0  |  ${dataFormatada}`, {
    size: 22, color: "666666",
    align: AlignmentType.CENTER, spaceAfter: 80,
  }),
  p("Backend: https://clima-solar-production.up.railway.app", {
    size: 20, color: "888888",
    align: AlignmentType.CENTER, spaceAfter: 20,
  }),
  p("Frontend: https://thomasak47.github.io/clima-solar/", {
    size: 20, color: "888888",
    align: AlignmentType.CENTER, spaceAfter: 20,
  }),
  p("Repositorio: https://github.com/ThomasAK47/clima-solar", {
    size: 20, color: "888888",
    align: AlignmentType.CENTER, spaceAfter: 0,
  }),
  new Paragraph({ children: [new PageBreak()] }),
];

// ─────────────────────────────────────────────────────────────────────────────
// SUMÁRIO
// ─────────────────────────────────────────────────────────────────────────────
const sumario = [
  p("Sumario", { heading: HeadingLevel.HEADING_1 }),
  new TableOfContents("Sumario", {
    hyperlink: true,
    headingStyleRange: "1-2",
  }),
  new Paragraph({ children: [new PageBreak()] }),
];

// ─────────────────────────────────────────────────────────────────────────────
// RODAPÉ E CABEÇALHO
// ─────────────────────────────────────────────────────────────────────────────
const footer = new Footer({
  children: [
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 80, after: 0 },
      children: [
        new TextRun({ text: "Clima Solar — Documentacao Tecnica  |  Pagina ", font: "Arial", size: 18, color: "888888" }),
        new TextRun({ children: [PageNumber.CURRENT], font: "Arial", size: 18, color: "888888" }),
        new TextRun({ text: " de ", font: "Arial", size: 18, color: "888888" }),
        new TextRun({ children: [PageNumber.TOTAL_PAGES], font: "Arial", size: 18, color: "888888" }),
      ],
    }),
  ],
});

// ─────────────────────────────────────────────────────────────────────────────
// DOCUMENTO
// ─────────────────────────────────────────────────────────────────────────────
const doc = new Document({
  numbering: {
    config: [
      {
        reference: "bullets",
        levels: [{
          level: 0,
          format: LevelFormat.BULLET,
          text: "•",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } },
        }],
      },
    ],
  },
  styles: {
    default: {
      document: { run: { font: "Arial", size: 24, color: BLACK } },
    },
    paragraphStyles: [
      {
        id: "Heading1",
        name: "Heading 1",
        basedOn: "Normal",
        next: "Normal",
        quickFormat: true,
        run: { size: 36, bold: true, font: "Arial", color: NAVY },
        paragraph: {
          spacing: { before: 360, after: 160 },
          outlineLevel: 0,
          border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: NAVY, space: 4 } },
        },
      },
      {
        id: "Heading2",
        name: "Heading 2",
        basedOn: "Normal",
        next: "Normal",
        quickFormat: true,
        run: { size: 28, bold: true, font: "Arial", color: NAVY },
        paragraph: { spacing: { before: 240, after: 120 }, outlineLevel: 1 },
      },
    ],
  },
  sections: [
    {
      properties: {
        page: {
          size: { width: PAGE_W, height: PAGE_H },
          margin: { top: MARGIN, right: MARGIN, bottom: MARGIN, left: MARGIN },
        },
      },
      footers: { default: footer },
      children: [
        ...capa,
        ...sumario,
        ...sec1,
        ...sec2,
        ...sec3,
        ...sec4,
        ...sec5,
        ...sec6,
        ...sec7,
        ...sec8,
      ],
    },
  ],
});

Packer.toBuffer(doc).then((buffer) => {
  const outPath = "relatorio_tecnico_clima_solar.docx";
  fs.writeFileSync(outPath, buffer);
  console.log(`✓ Gerado: ${outPath} (${(buffer.length / 1024).toFixed(1)} KB)`);
}).catch((err) => {
  console.error("Erro ao gerar .docx:", err);
  process.exit(1);
});
