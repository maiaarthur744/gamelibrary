# Game Library

Uma página local que mostra **todos os seus jogos de várias lojas num lugar só**: Steam, GOG e Epic Games, mais uma lista manual para o que não tem API (Battle.net, Amazon Games etc.).

Não é um launcher: ele só lista o que você tem. O destaque é **agrupar o mesmo jogo comprado em lojas diferentes**, para você ver o que está repetido e onde.

Tudo roda na sua máquina. Seus dados ficam em uma pasta local e nada é enviado para servidor nenhum (além das chamadas às próprias lojas).

## Funcionalidades

- Ver a biblioteca unificada, em **grade com capas** ou em **lista**
- Ver quais jogos estão em **mais de uma loja**
- Filtrar por loja
- Buscar por nome ou tag e ordenar por nome, plataformas, horas jogadas ou jogado recentemente
- Marcar **status** (um jogo pode ter vários ao mesmo tempo): Jogando, Jogado, Para jogar, Para jogar novamente, Abandonado, Multiplayer/Co-op, Sem fim e Não jogado
- Dar uma **nota de 1 a 5 estrelas**, escrever **anotações ou um review** e registrar a **data de início e de fim** de cada jogo
- **Adicionar jogos manualmente** pela interface, sem editar os arquivos na mão
- **Trocar a capa** de qualquer jogo por um link ou por uma imagem do seu computador
- Criar **tags** com os nomes que quiser e filtrar por elas
- **Ocultar** jogos que você não quer ver
- **Separar** jogos que foram agrupados por engano

## Requisitos

- **Python 3.11 ou mais novo** (`python3 --version` para conferir)
- Contas nas lojas que você quer listar
- Para a Steam: uma chave de API gratuita

Foi desenvolvido e testado no **macOS**. Deve funcionar em Linux e Windows, mas não foi testado lá.

## Instalação

```bash
git clone <endereço do repositório>
cd gamelibrary

python3 -m venv .venv
source .venv/bin/activate        # no Windows: .venv\Scripts\activate
pip install -e .
```

Depois de ativar o ambiente, o comando `gamelibrary` fica disponível.

## Conectando suas contas

Você não precisa configurar todas as lojas. Conecte só as que usa e rode o `sync` para elas.

### Steam

1. Gere uma chave de API em <https://steamcommunity.com/dev/apikey> (qualquer nome de domínio serve, por exemplo `localhost`).
2. Descubra seu **SteamID64**, um número de 17 dígitos. Cole a URL do seu perfil em <https://steamid.io> e copie o "steamID64".
3. No Steam, deixe **Detalhes dos jogos** como **Público**. Sem isso a API devolve uma lista vazia.
4. Copie o arquivo de exemplo e preencha:

   ```bash
   cp .env.example .env
   ```

   ```
   STEAM_API_KEY=sua_chave_aqui
   STEAM_ID=seu_steam_ID_aqui
   ```

5. Rode:

   ```bash
   gamelibrary sync steam
   ```

### GOG

O login é feito uma vez pelo navegador. Não há chave para configurar.

1. Rode:

   ```bash
   gamelibrary gog-login
   ```

2. O programa mostra uma URL. Abra no navegador e faça login na sua conta GOG.
3. Depois do login você cai numa página em branco, é normal. Copie a **URL inteira** da barra de endereço (ela termina com `?code=...`).
4. Cole essa URL no terminal, onde ele pede `URL or code:`, e aperte Enter.
5. Rode:

   ```bash
   gamelibrary sync gog
   ```

O token fica salvo em `data/gog_token.json` e é renovado automaticamente. Só repita o login se o `sync` avisar que você não está autenticado.

### Epic Games

A Epic não tem API pública de biblioteca. Por isso o projeto usa o [Legendary](https://github.com/derrod/legendary), uma ferramenta open source que já vem instalada.

1. Rode:

   ```bash
   gamelibrary epic-login
   ```

2. Ele abre a página de login da Epic no navegador. Depois do login aparece um JSON com um campo `authorizationCode`. Copie só o valor (sem aspas) e cole no terminal quando ele pedir.
3. Rode:

   ```bash
   gamelibrary sync epic
   ```

A sessão da Epic é guardada pelo próprio Legendary, no perfil do seu usuário (fora da pasta do projeto).

### Battle.net, Amazon Games e outros (manual)

Essas lojas não oferecem uma forma de listar a biblioteca via API, então você adiciona os jogos à mão. Há duas maneiras.

**Pela interface.** Abra o app e clique em **+ Adicionar jogo**. Escolha a plataforma (ou "Outra…" para digitar o nome de uma nova), informe o título e, se quiser, o link da capa, as horas jogadas e o link da loja. O jogo é gravado em `data/manual.json` e aparece na hora, sem precisar rodar `sync`. Para apagar um jogo adicionado assim, abra o jogo e clique em **Remover jogo** ao lado da licença.

**Editando o arquivo.** Se preferir, copie o modelo e edite:

```bash
cp data/manual.example.json data/manual.json
```

```json
[
  {
    "platform": "battlenet",
    "title": "Diablo IV",
    "cover_url": "https://exemplo.com/diablo4.jpg",
    "playtime_minutes": 1200,
    "url": "https://..."
  }
]
```

- `platform` e `title` são obrigatórios. `battlenet` e `amazon` já têm nome e cor próprios na interface. Qualquer outro nome funciona, com uma cor cinza.
- `steam`, `gog` e `epic` **não podem** ser usados aqui, porque essas lojas são sincronizadas pela própria API.
- `cover_url` (link de uma imagem), `playtime_minutes`, `url` e `platform_id` são opcionais.

Depois de editar o arquivo à mão, rode `gamelibrary sync manual` e recarregue a página.

## Usando

```bash
gamelibrary sync            # atualiza todas as lojas de uma vez
gamelibrary sync steam      # ou só uma: steam, gog, epic, manual
gamelibrary serve           # abre a interface em http://127.0.0.1:8765
```

Para atualizar a biblioteca depois de comprar jogos novos, rode `gamelibrary sync` e recarregue a página.

Se uma loja falhar, as outras continuam e o erro aparece no terminal.

Outras opções:

- `gamelibrary serve --port 9000` usa outra porta, e `--no-browser` não abre o navegador sozinho.
- `gamelibrary list -s witcher` busca no terminal, e `-p gog` filtra por loja.

## Como funciona

```
Steam (API oficial) ──┐
GOG (endpoints do site)├─► providers ─► data/library.db (SQLite) ─► agrupamento ─► interface web
Epic (via Legendary) ──┤                                                           (localhost)
manual.json ───────────┘
```

- Cada loja tem um **provider** em `gamelibrary/providers/` que devolve a lista de jogos no mesmo formato. O `sync` apaga e regrava os jogos daquela loja no banco, então jogos removidos da conta também somem.
- O **agrupamento** (`gamelibrary/grouping.py`) compara os títulos ignorando ™, ®, acentos, pontuação e o "The" do início, e descarta sufixos de edição como "Game of the Year Edition", "Definitive Edition" e "Director's Cut".
- O servidor (`gamelibrary/server.py`) só aceita conexões da própria máquina e serve a página em `gamelibrary/static/index.html`, sem nenhuma etapa de build.

### Capas

As capas vêm das próprias lojas: Steam, GOG e Epic. Se uma capa vier cortada, abra o jogo e escolha outra em **Capas disponíveis**. Se um jogo ficar sem capa, ou se você preferir outra, use a linha **Capa**: cole o link de uma imagem e clique em **Usar link**, ou use **Enviar arquivo** para mandar uma imagem do seu computador (PNG, JPG ou WebP, até 5 MB; o site [SteamGridDB](https://www.steamgriddb.com) tem boas capas). **Voltar à automática** desfaz a troca. Imagens enviadas ficam em `data/covers/`.

### Quando o agrupamento erra

Como a comparação é só pelo título, às vezes dois jogos diferentes são agrupados, ou o mesmo jogo fica separado.

- **Juntou jogos diferentes:** abra o jogo e clique em **Separar** na licença errada. Ela vira um card próprio. **Desfazer separação** agrupa de novo. Vale na hora, sem precisar de `sync`.
- **Não juntou o mesmo jogo:** crie o arquivo `data/aliases.json`, associando um título a outro:

  ```json
  { "Título em uma loja": "Título na outra loja" }
  ```

Tags, status, nota, anotações e ocultação ficam ligados ao grupo. Um card criado ao separar começa sem eles.

## Onde ficam seus dados

Tudo o que é seu fica fora do controle de versão (`.gitignore`):

| Arquivo               | Conteúdo                                                                    |
| --------------------- | --------------------------------------------------------------------------- |
| `.env`                | chave da Steam e SteamID                                                    |
| `data/library.db`     | biblioteca, status, notas, reviews, datas, tags, jogos ocultos e separações |
| `data/gog_token.json` | token de login da GOG                                                       |
| `data/manual.json`    | sua lista manual                                                            |
| `data/covers/`        | imagens de capa que você enviou                                             |
| `data/aliases.json`   | junções manuais de títulos                                                  |

Se for compartilhar o projeto, **não envie esses arquivos**. Cada pessoa cria os próprios, seguindo os passos acima.

## Limitações

- **GOG e Epic usam caminhos não oficiais.** A GOG usa os mesmos endpoints do site e do cliente GOG Galaxy, e a Epic depende do Legendary. Isso funciona hoje, mas pode quebrar sem aviso se as lojas mudarem algo.
- **Battle.net não lista a conta inteira** na API pública, por isso é manual.
- **Jogos de outras fontes na Epic:** a listagem inclui jogos resgatados de terceiros, mas eles podem aparecer sem link para a loja.
- **Horas jogadas** só existem para a Steam (e para o que você escrever no `manual.json`).

## Problemas comuns

| Sintoma                                                  | O que fazer                                       |
| -------------------------------------------------------- | ------------------------------------------------- |
| `steam: FAILED - Set STEAM_API_KEY and STEAM_ID in .env` | Crie e preencha o `.env` (veja a seção da Steam). |
| `Steam returned no games`                                | Deixe "Detalhes dos jogos" público no seu perfil. |
| `Not logged in to GOG`                                   | Rode `gamelibrary gog-login`.                     |
| `legendary failed (not logged in?)`                      | Rode `gamelibrary epic-login`.                    |
| `command not found: gamelibrary`                         | Ative o ambiente: `source .venv/bin/activate`.    |
| A página abre vazia                                      | Rode `gamelibrary sync` antes do `serve`.         |
