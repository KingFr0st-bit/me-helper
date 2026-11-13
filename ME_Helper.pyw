"""
⚡ ME HELPER v5 — My Electric Helper
Dimensionamento elétrico residencial (NBR 5410) + assistente de normas com IA.

Autor: Davi Ribeiro Nolasco Sales — Engenharia Civil, UFRJ Macaé
Disciplina: Programação 2

COMO RODAR: dois cliques neste arquivo (precisa do Python 3.10+ instalado).
            ou no terminal:  python ME_Helper.pyw

ESTRUTURA DO CÓDIGO (cada camada só usa as camadas anteriores):
    1. Normas          -> números da NBR 5410
    2. Exceções        -> erros próprios
    3. Modelos (POO)   -> Ambiente e subclasses, TUE, Projeto
    4. Cálculos        -> disjuntor, cabo, queda de tensão, quadro
    5. Persistência    -> JSON, CSV, memorial HTML
    6. IA              -> Groq / Gemini / Ollama / Offline
    7. Interface       -> telas em Tkinter
"""
from __future__ import annotations

import csv
import html
import json
import math
import os
import queue
import threading
import tkinter as tk
import unicodedata
import urllib.error
import urllib.request
import uuid
import webbrowser
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import ClassVar, Iterable

__version__ = "5.0"

##############################################################################
#  CAMADA 1 · NORMAS
#  Tabelas e constantes da NBR 5410 (Iz, FCA, FCT, disjuntores...)
##############################################################################
# Tabelas e constantes da NBR 5410:2004 usadas pelo ME Helper.
#
# Tudo que é "número de norma" mora aqui. Assim, se um professor ou a
# revisão da norma mudar um valor, só esse arquivo é alterado
# (princípio da responsabilidade única).

# --- Disjuntores termomagnéticos comerciais (A) ---------------------------
# Boa prática de obra: nada abaixo de 10 A em circuito residencial
# (disjuntor de 6 A existe, mas desarma à toa e é difícil de achar).
DISJUNTOR_MINIMO = 10
DISJUNTORES_COMERCIAIS: tuple[int, ...] = (6, 10, 13, 16, 20, 25, 32, 40, 50, 63, 80, 100, 125)

# --- Capacidade de condução de corrente (Iz) ------------------------------
# Tabela 36: cobre, isolação PVC 70 °C, método de referência B1
# (condutores isolados em eletroduto embutido em alvenaria),
# temperatura ambiente 30 °C, 2 condutores carregados (F+N ou F+F).
IZ_B1_2_CONDUTORES: dict[float, float] = {
    1.5: 17.5, 2.5: 24.0, 4.0: 32.0, 6.0: 41.0, 10.0: 57.0,
    16.0: 76.0, 25.0: 101.0, 35.0: 125.0, 50.0: 151.0,
}
SECOES_COMERCIAIS: tuple[float, ...] = tuple(sorted(IZ_B1_2_CONDUTORES))

# --- Seções mínimas (6.2.6.1) ----------------------------------------------
SECAO_MINIMA = {"iluminacao": 1.5, "forca": 2.5}

# --- Fator de correção por agrupamento (Tabela 42, em eletroduto) ----------
FCA_AGRUPAMENTO: dict[int, float] = {
    1: 1.00, 2: 0.80, 3: 0.70, 4: 0.65, 5: 0.60, 6: 0.57,
    7: 0.54, 8: 0.52, 9: 0.50, 12: 0.45, 16: 0.41, 20: 0.38,
}

# --- Fator de correção de temperatura, PVC (Tabela 40) ---------------------
FCT_TEMPERATURA_PVC: dict[int, float] = {
    10: 1.22, 15: 1.17, 20: 1.12, 25: 1.06, 30: 1.00,
    35: 0.94, 40: 0.87, 45: 0.79, 50: 0.71,
}

# --- Queda de tensão --------------------------------------------------------
RESISTIVIDADE_COBRE = 0.0172      # Ω·mm²/m a 20 °C (valor didático usual)
QUEDA_MAX_TERMINAL = 4.0          # % permitida em circuito terminal (6.2.7)

# --- Iluminação (9.5.2.1.2) -------------------------------------------------
ILUM_BASE_VA = 100                # até 6 m²
ILUM_AREA_BASE = 6.0
ILUM_INCREMENTO_VA = 60           # a cada 4 m² inteiros acima de 6 m²
ILUM_INCREMENTO_AREA = 4.0

# --- TUG (9.5.2.2) ----------------------------------------------------------
TUG_PERIMETRO_SECO = 5.0          # 1 tomada a cada 5 m (salas, quartos...)
TUG_PERIMETRO_MOLHADO = 3.5       # 1 tomada a cada 3,5 m (cozinha, A.S....)
TUG_VA_MOLHADO = 600
TUG_VA_PADRAO = 100

# --- Divisão de circuitos (9.5.3) ------------------------------------------
CORRENTE_MAX_TUE_COMPARTILHADA = 10.0  # acima disso, circuito independente

# Limites "de boa prática" pra agrupar cargas num mesmo circuito
LIMITE_CORRENTE_ILUMINACAO = 10.0
LIMITE_CORRENTE_TUG = 16.0

# --- Fatores de potência típicos (conversão VA -> W) ------------------------
FP_ILUMINACAO = 1.0
FP_TUG = 0.8


def fator_agrupamento(n_circuitos: int) -> float:
    """Retorna o FCA para n circuitos no mesmo eletroduto.

    Para valores fora da tabela usa o fator da linha imediatamente
    acima (a favor da segurança).
    """
    if n_circuitos < 1:
        raise ValueError("Número de circuitos agrupados deve ser >= 1")
    chaves = sorted(FCA_AGRUPAMENTO)
    for k in chaves:
        if n_circuitos <= k:
            return FCA_AGRUPAMENTO[k]
    return FCA_AGRUPAMENTO[chaves[-1]]


def fator_temperatura(temp_c: int) -> float:
    """FCT para PVC. Arredonda a temperatura pra cima (a favor da segurança)."""
    chaves = sorted(FCT_TEMPERATURA_PVC)
    for k in chaves:
        if temp_c <= k:
            return FCT_TEMPERATURA_PVC[k]
    raise ValueError("Temperatura acima de 50 °C fora da tabela para PVC")


def espacos_reserva(n_circuitos: int) -> int:
    """Espaço de reserva mínimo no quadro (6.5.4.7)."""
    if n_circuitos <= 6:
        return 2
    if n_circuitos <= 12:
        return 3
    if n_circuitos <= 30:
        return 4
    return round(n_circuitos * 0.15 + 0.4999)

##############################################################################
#  CAMADA 2 · EXCEÇÕES
#  Erros próprios do app, em hierarquia
##############################################################################
# Exceções próprias do ME Helper.
#
# Ter uma hierarquia própria permite que a interface trate cada tipo de
# erro de um jeito (mensagem amigável) sem engolir bugs de verdade.

class MEHelperErro(Exception):
    """Raiz de todos os erros do app."""


class DadosInvalidosErro(MEHelperErro):
    """Entrada do usuário inválida (área negativa, nome vazio...)."""


class AmbienteDuplicadoErro(MEHelperErro):
    """Já existe um ambiente com esse nome no projeto."""


class DimensionamentoErro(MEHelperErro):
    """Não foi possível dimensionar (corrente acima da tabela, etc.)."""


class PersistenciaErro(MEHelperErro):
    """Falha ao salvar/abrir arquivo de projeto."""


class IAErro(MEHelperErro):
    """Falha na comunicação com o provedor de IA."""

##############################################################################
#  CAMADA 3 · MODELOS (POO)
#  Ambiente (abstrata) + subclasses, TUE e Projeto
##############################################################################
# Modelos de domínio: ambientes, tomadas específicas e o projeto.
#
# Conceitos de Prog 2 aplicados aqui:
# - dataclasses (menos boilerplate, __init__/__repr__ automáticos)
# - classe abstrata (ABC) + herança: cada tipo de ambiente sabe aplicar
#   a SUA regra de TUG da NBR 5410 (polimorfismo)
# - registro de subclasses + fábrica (criar_ambiente)
# - @property para valores derivados (nunca ficam desatualizados)
# - serialização to_dict/from_dict para salvar em JSON

# ---------------------------------------------------------------------------
# TUE - Tomada de Uso Específico
# ---------------------------------------------------------------------------
@dataclass
class TUE:
    nome: str
    potencia_w: float
    tensao: int = 127
    fp: float = 1.0  # chuveiro/forno resistivo = 1.0; motor/ar ~0.8-0.9

    def __post_init__(self) -> None:
        self.nome = self.nome.strip()
        if not self.nome:
            raise DadosInvalidosErro("O equipamento precisa de um nome.")
        if self.potencia_w <= 0:
            raise DadosInvalidosErro(f"Potência de '{self.nome}' deve ser maior que zero.")
        if self.tensao not in (127, 220):
            raise DadosInvalidosErro("Tensão da TUE deve ser 127 V ou 220 V.")
        if not 0.5 <= self.fp <= 1.0:
            raise DadosInvalidosErro("Fator de potência deve estar entre 0,5 e 1,0.")

    @property
    def potencia_va(self) -> float:
        return self.potencia_w / self.fp

    @property
    def corrente(self) -> float:
        return self.potencia_va / self.tensao

    def to_dict(self) -> dict:
        return {"nome": self.nome, "potencia_w": self.potencia_w, "tensao": self.tensao, "fp": self.fp}

    @classmethod
    def from_dict(cls, d: dict) -> "TUE":
        return cls(d["nome"], float(d["potencia_w"]), int(d.get("tensao", 127)), float(d.get("fp", 1.0)))


# ---------------------------------------------------------------------------
# Ambiente (classe abstrata) e subclasses
# ---------------------------------------------------------------------------
@dataclass
class Ambiente(ABC):
    nome: str
    area: float
    perimetro: float
    distancia_qd: float = 10.0          # comprimento estimado do circuito (m)
    tug_extras: int = 0                 # tomadas além do mínimo da norma
    tues: list[TUE] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    # Cada subclasse preenche esses atributos de classe
    TIPO: ClassVar[str] = ""
    PRECISA_DR: ClassVar[bool] = False          # 5.1.3.2.2
    CIRCUITO_TUG_EXCLUSIVO: ClassVar[bool] = False  # 9.5.3.3

    _registro: ClassVar[dict[str, type["Ambiente"]]] = {}

    def __init_subclass__(cls, **kwargs) -> None:
        """Toda subclasse com TIPO entra sozinha no registro da fábrica."""
        super().__init_subclass__(**kwargs)
        if cls.TIPO:
            Ambiente._registro[cls.TIPO] = cls

    def __post_init__(self) -> None:
        self.nome = self.nome.strip()
        if not self.nome:
            raise DadosInvalidosErro("Dê um nome ao cômodo.")
        if self.area <= 0:
            raise DadosInvalidosErro("A área deve ser maior que zero.")
        if self.perimetro <= 0:
            raise DadosInvalidosErro("O perímetro deve ser maior que zero.")
        if self.distancia_qd <= 0:
            raise DadosInvalidosErro("A distância até o quadro deve ser maior que zero.")
        if self.tug_extras < 0:
            raise DadosInvalidosErro("TUGs extras não pode ser negativo.")
        # Desigualdade isoperimétrica: nenhuma figura tem P < 2·√(π·A).
        if self.perimetro < 2 * math.sqrt(math.pi * self.area) - 1e-9:
            raise DadosInvalidosErro(
                f"Perímetro {self.perimetro:g} m é geometricamente impossível para "
                f"{self.area:g} m² (mínimo {2 * math.sqrt(math.pi * self.area):.2f} m). "
                "Confere se não trocou área e perímetro?")

    # ----- regras de iluminação (iguais pra todo mundo) -----
    @property
    def iluminacao_va(self) -> int:
        if self.area <= ILUM_AREA_BASE:
            return ILUM_BASE_VA
        incrementos = int((self.area - ILUM_AREA_BASE) // ILUM_INCREMENTO_AREA)
        return ILUM_BASE_VA + incrementos * ILUM_INCREMENTO_VA

    # ----- regras de TUG (cada subclasse implementa a sua) -----
    @abstractmethod
    def qtd_tug_minima(self) -> int:
        """Quantidade mínima de TUGs exigida pela norma."""

    def potencias_tug(self) -> list[int]:
        """VA de cada tomada. Padrão: 100 VA por ponto."""
        return [TUG_VA_PADRAO] * self.qtd_tug

    @property
    def qtd_tug(self) -> int:
        return self.qtd_tug_minima() + self.tug_extras

    @property
    def tug_va(self) -> int:
        return sum(self.potencias_tug())

    @property
    def tue_va(self) -> float:
        return sum(t.potencia_va for t in self.tues)

    @property
    def total_va(self) -> float:
        return self.iluminacao_va + self.tug_va + self.tue_va

    @property
    def avisos(self) -> list[str]:
        """Alertas que não impedem o cálculo, mas merecem atenção."""
        msgs = []
        if self.perimetro < 4 * math.sqrt(self.area) - 1e-9:
            msgs.append("Perímetro menor que o de um quadrado de mesma área: confira as medidas.")
        for t in self.tues:
            if t.corrente > 32:
                msgs.append(f"{t.nome}: corrente alta ({t.corrente:.1f} A). Avalie 220 V.")
        return msgs

    def memoria_tug(self) -> str:
        """Texto explicando o cálculo (vai pro memorial)."""
        return f"{self.qtd_tug_minima()} TUG(s) mínimas"

    # ----- serialização -----
    def to_dict(self) -> dict:
        return {
            "id": self.id, "tipo": self.TIPO, "nome": self.nome, "area": self.area,
            "perimetro": self.perimetro, "distancia_qd": self.distancia_qd,
            "tug_extras": self.tug_extras, "tues": [t.to_dict() for t in self.tues],
        }

    @staticmethod
    def from_dict(d: dict) -> "Ambiente":
        return criar_ambiente(
            d["tipo"], nome=d["nome"], area=float(d["area"]), perimetro=float(d["perimetro"]),
            distancia_qd=float(d.get("distancia_qd", 10.0)), tug_extras=int(d.get("tug_extras", 0)),
            tues=[TUE.from_dict(t) for t in d.get("tues", [])], id=d.get("id") or uuid.uuid4().hex[:8])


def _teto(valor: float, passo: float) -> int:
    """Quantas tomadas cabem: uma a cada `passo` metros, fração conta como inteira."""
    return max(1, math.ceil(round(valor / passo, 9)))


def _potencias_area_molhada(qtd: int) -> list[int]:
    """600 VA até 3 pontos, 100 VA nos excedentes (9.5.2.2.2-a)."""
    return [TUG_VA_MOLHADO if i < 3 else TUG_VA_PADRAO for i in range(qtd)]


class SalaDormitorio(Ambiente):
    TIPO = "Sala / Dormitório"

    def qtd_tug_minima(self) -> int:
        return _teto(self.perimetro, TUG_PERIMETRO_SECO)

    def memoria_tug(self) -> str:
        return f"⌈{self.perimetro} / 5⌉ = {self.qtd_tug_minima()} TUG(s) × 100 VA"


class CozinhaAreaServico(Ambiente):
    TIPO = "Cozinha / Copa / Área de Serviço / Lavanderia"
    PRECISA_DR = True
    CIRCUITO_TUG_EXCLUSIVO = True

    def qtd_tug_minima(self) -> int:
        return _teto(self.perimetro, TUG_PERIMETRO_MOLHADO)

    def potencias_tug(self) -> list[int]:
        return _potencias_area_molhada(self.qtd_tug)

    def memoria_tug(self) -> str:
        return (f"⌈{self.perimetro} / 3,5⌉ = {self.qtd_tug_minima()} TUG(s); "
                "600 VA nas 3 primeiras e 100 VA nas demais")


class Banheiro(Ambiente):
    TIPO = "Banheiro"
    PRECISA_DR = True

    def qtd_tug_minima(self) -> int:
        return 1  # junto ao lavatório

    def potencias_tug(self) -> list[int]:
        return _potencias_area_molhada(self.qtd_tug)

    def memoria_tug(self) -> str:
        return "1 TUG junto ao lavatório × 600 VA"


class Varanda(Ambiente):
    TIPO = "Varanda / Área externa"
    PRECISA_DR = True

    def qtd_tug_minima(self) -> int:
        return 1

    def memoria_tug(self) -> str:
        return "mínimo de 1 TUG × 100 VA"


class OutroComodo(Ambiente):
    TIPO = "Outros (hall, corredor, closet...)"

    def qtd_tug_minima(self) -> int:
        if self.area <= ILUM_AREA_BASE:
            return 1
        return _teto(self.perimetro, TUG_PERIMETRO_SECO)

    def memoria_tug(self) -> str:
        if self.area <= ILUM_AREA_BASE:
            return "área ≤ 6 m² → 1 TUG × 100 VA"
        return f"⌈{self.perimetro} / 5⌉ = {self.qtd_tug_minima()} TUG(s) × 100 VA"


def tipos_disponiveis() -> list[str]:
    return list(Ambiente._registro)


def criar_ambiente(tipo: str, **kwargs) -> Ambiente:
    """Fábrica: devolve a subclasse certa a partir do nome do tipo."""
    try:
        classe = Ambiente._registro[tipo]
    except KeyError:
        raise DadosInvalidosErro(f"Tipo de ambiente desconhecido: {tipo}") from None
    return classe(**kwargs)


# ---------------------------------------------------------------------------
# Projeto
# ---------------------------------------------------------------------------
@dataclass
class Projeto:
    nome: str = "Projeto sem nome"
    tensao: int = 127                 # tensão das cargas F-N (iluminação e TUG)
    circuitos_agrupados: int = 3      # pior trecho de eletroduto
    temperatura_ambiente: int = 30
    ambientes: list[Ambiente] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.tensao not in (127, 220):
            raise DadosInvalidosErro("Tensão deve ser 127 V ou 220 V.")

    # ----- CRUD -----
    def buscar(self, nome: str) -> Ambiente | None:
        alvo = nome.strip().casefold()
        return next((a for a in self.ambientes if a.nome.casefold() == alvo), None)

    def adicionar(self, amb: Ambiente) -> None:
        if self.buscar(amb.nome):
            raise AmbienteDuplicadoErro(f"Já existe um cômodo chamado '{amb.nome}'.")
        self.ambientes.append(amb)

    def substituir(self, id_antigo: str, novo: Ambiente) -> None:
        """Edição segura: só troca depois que o novo ambiente foi validado."""
        conflito = self.buscar(novo.nome)
        if conflito and conflito.id != id_antigo:
            raise AmbienteDuplicadoErro(f"Já existe um cômodo chamado '{novo.nome}'.")
        for i, a in enumerate(self.ambientes):
            if a.id == id_antigo:
                novo.id = id_antigo
                self.ambientes[i] = novo
                return
        raise DadosInvalidosErro("Cômodo a ser editado não foi encontrado.")

    def remover(self, id_amb: str) -> None:
        self.ambientes = [a for a in self.ambientes if a.id != id_amb]

    # ----- totais -----
    @property
    def total_iluminacao_va(self) -> int:
        return sum(a.iluminacao_va for a in self.ambientes)

    @property
    def total_tug_va(self) -> int:
        return sum(a.tug_va for a in self.ambientes)

    @property
    def total_tue_va(self) -> float:
        return sum(a.tue_va for a in self.ambientes)

    @property
    def total_tue_w(self) -> float:
        return sum(t.potencia_w for a in self.ambientes for t in a.tues)

    @property
    def total_va(self) -> float:
        return self.total_iluminacao_va + self.total_tug_va + self.total_tue_va

    @property
    def total_w(self) -> float:
        """Potência ativa estimada (VA × FP típico de cada carga)."""
        return (self.total_iluminacao_va * FP_ILUMINACAO
                + self.total_tug_va * FP_TUG + self.total_tue_w)

    # ----- serialização -----
    def to_dict(self) -> dict:
        return {
            "versao_formato": 1, "nome": self.nome, "tensao": self.tensao,
            "circuitos_agrupados": self.circuitos_agrupados,
            "temperatura_ambiente": self.temperatura_ambiente,
            "ambientes": [a.to_dict() for a in self.ambientes],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Projeto":
        p = cls(nome=d.get("nome", "Projeto"), tensao=int(d.get("tensao", 127)),
                circuitos_agrupados=int(d.get("circuitos_agrupados", 3)),
                temperatura_ambiente=int(d.get("temperatura_ambiente", 30)))
        for a in d.get("ambientes", []):
            p.adicionar(Ambiente.from_dict(a))
        return p

##############################################################################
#  CAMADA 4 · CÁLCULOS
#  Dimensionamento de circuitos e montagem do quadro
##############################################################################
# Dimensionamento de circuitos e montagem do quadro de distribuição.
#
# Melhorias em relação à versão de Prog 1:
# - coordenação real disjuntor x condutor (Ib <= In <= Iz corrigida)
# - fatores de agrupamento e temperatura
# - verificação de queda de tensão (4% no circuito terminal)
# - circuitos agrupados de forma inteligente (bin packing "first fit
#   decreasing") em vez de 2 circuitos fixos por cômodo
# - TUEs acima de 10 A em circuito independente e indicação de DR

TIPO_ILUM, TIPO_TUG, TIPO_TUE = "Iluminação", "TUG", "TUE"


@dataclass
class Circuito:
    descricao: str
    tipo: str
    potencia_va: float
    tensao: int
    comprimento: float
    dr: bool = False
    cargas: list[str] = field(default_factory=list)
    numero: int = 0
    # Preenchidos por dimensionar()
    corrente: float = 0.0
    disjuntor: int = 0
    secao: float = 0.0
    iz_corrigida: float = 0.0
    queda_pct: float = 0.0
    criterio: str = ""

    def dimensionar(self, fca: float = 1.0, fct: float = 1.0) -> "Circuito":
        """Escolhe disjuntor e seção. Retorna o próprio circuito (encadeável)."""
        if self.potencia_va <= 0:
            raise DimensionamentoErro(f"{self.descricao}: potência nula.")
        self.corrente = self.potencia_va / self.tensao
        self.disjuntor = escolher_disjuntor(self.corrente)
        minima = SECAO_MINIMA["iluminacao" if self.tipo == TIPO_ILUM else "forca"]

        for s in SECOES_COMERCIAIS:
            if s < minima:
                continue
            iz = IZ_B1_2_CONDUTORES[s] * fca * fct
            queda = queda_tensao_pct(self.corrente, self.comprimento, s, self.tensao)
            if iz >= self.disjuntor and queda <= QUEDA_MAX_TERMINAL:
                self.secao, self.iz_corrigida, self.queda_pct = s, iz, queda
                if s == minima:
                    self.criterio = "seção mínima"
                elif IZ_B1_2_CONDUTORES[_secao_anterior(s)] * fca * fct >= self.disjuntor:
                    # a seção anterior aguentava a corrente, então quem reprovou foi a queda
                    self.criterio = "queda de tensão"
                else:
                    self.criterio = "capacidade de corrente"
                return self
        raise DimensionamentoErro(
            f"{self.descricao}: nenhuma seção até {SECOES_COMERCIAIS[-1]} mm² atende. "
            "Divida a carga ou use 220 V.")


def _secao_anterior(s: float) -> float:
    i = SECOES_COMERCIAIS.index(s)
    return SECOES_COMERCIAIS[max(0, i - 1)]


def escolher_disjuntor(corrente: float) -> int:
    """Menor disjuntor comercial com In >= Ib (respeitando o mínimo de 10 A)."""
    for d in DISJUNTORES_COMERCIAIS:
        if d >= corrente and d >= DISJUNTOR_MINIMO:
            return d
    raise DimensionamentoErro(
        f"Corrente de {corrente:.1f} A acima do maior disjuntor da tabela "
        f"({DISJUNTORES_COMERCIAIS[-1]} A).")


def queda_tensao_pct(corrente: float, comprimento: float, secao: float, tensao: int) -> float:
    """Queda de tensão percentual em circuito de 2 condutores (ida e volta)."""
    dv = 2 * RESISTIVIDADE_COBRE * comprimento * corrente / secao
    return dv / tensao * 100


# ---------------------------------------------------------------------------
# Agrupamento de cargas em circuitos
# ---------------------------------------------------------------------------
@dataclass
class _Item:
    nome: str
    va: float
    dist: float


def _empacotar(itens: Iterable[_Item], limite_va: float) -> list[list[_Item]]:
    """First Fit Decreasing: ordena do maior pro menor e encaixa no 1º que couber."""
    caixas: list[list[_Item]] = []
    somas: list[float] = []
    for it in sorted(itens, key=lambda i: i.va, reverse=True):
        for k, soma in enumerate(somas):
            if soma + it.va <= limite_va:
                caixas[k].append(it)
                somas[k] += it.va
                break
        else:
            caixas.append([it])
            somas.append(it.va)
    return caixas


def _nome_circuito(prefixo: str, itens: list[_Item]) -> str:
    nomes = list(dict.fromkeys(i.nome for i in itens))  # sem repetir, mantendo ordem
    return f"{prefixo} {' + '.join(nomes)}"


def montar_quadro(projeto: Projeto) -> list[Circuito]:
    """Divide as cargas do projeto em circuitos e dimensiona cada um."""
    v = projeto.tensao
    lim_ilum = LIMITE_CORRENTE_ILUMINACAO * v
    lim_tug = LIMITE_CORRENTE_TUG * v
    grupos: dict[tuple, list[_Item]] = {}
    circuitos: list[Circuito] = []

    for amb in projeto.ambientes:
        grupos.setdefault((TIPO_ILUM, amb.PRECISA_DR), []).append(
            _Item(amb.nome, amb.iluminacao_va, amb.distancia_qd))

        chave_tug = ((TIPO_TUG, amb.PRECISA_DR, amb.id) if amb.CIRCUITO_TUG_EXCLUSIVO
                     else (TIPO_TUG, amb.PRECISA_DR))
        if amb.tug_va <= lim_tug:
            grupos.setdefault(chave_tug, []).append(_Item(amb.nome, amb.tug_va, amb.distancia_qd))
        else:  # cômodo grande demais pra 1 circuito: quebra por tomada
            grupos.setdefault(chave_tug, []).extend(
                _Item(amb.nome, va, amb.distancia_qd) for va in amb.potencias_tug())

        for tue in amb.tues:  # TUE: sempre circuito independente
            circuitos.append(Circuito(
                f"TUE {tue.nome} ({amb.nome})", TIPO_TUE, tue.potencia_va, tue.tensao,
                amb.distancia_qd, dr=amb.PRECISA_DR, cargas=[tue.nome]))

    for (tipo, dr, *_), itens in grupos.items():
        limite = lim_ilum if tipo == TIPO_ILUM else lim_tug
        prefixo = "Ilum." if tipo == TIPO_ILUM else "TUGs"
        for caixa in _empacotar(itens, limite):
            circuitos.append(Circuito(
                _nome_circuito(prefixo, caixa), tipo, sum(i.va for i in caixa), v,
                max(i.dist for i in caixa), dr=dr, cargas=[i.nome for i in caixa]))

    ordem = {TIPO_ILUM: 0, TIPO_TUG: 1, TIPO_TUE: 2}
    circuitos.sort(key=lambda c: (ordem[c.tipo], c.descricao))

    fca = fator_agrupamento(projeto.circuitos_agrupados)
    fct = fator_temperatura(projeto.temperatura_ambiente)
    for n, c in enumerate(circuitos, start=1):
        c.numero = n
        c.dimensionar(fca, fct)
    return circuitos


def revisao_local(projeto: Projeto, circuitos: list[Circuito]) -> list[str]:
    """Revisor por regras: funciona sem internet e sem IA."""
    obs: list[str] = []
    for c in circuitos:
        uso = c.corrente / c.disjuntor
        if uso > 0.9:
            obs.append(f"🔥 C{c.numero} ({c.descricao}) usa {uso:.0%} do disjuntor: quase sem folga, "
                       "considere dividir a carga.")
        if c.queda_pct > 3:
            obs.append(f"📉 C{c.numero} tem queda de {c.queda_pct:.2f}% (limite 4%). Aproxime o QD ou engrosse o cabo.")
        if c.tipo == TIPO_TUE and c.tensao == 127 and c.corrente > 10:
            obs.append(f"⚡ C{c.numero} ({c.descricao}): {c.corrente:.1f} A em 127 V. Em 220 V a corrente cai "
                       "pela metade e o cabo fica mais fino.")
    for a in projeto.ambientes:
        obs += [f"📐 {a.nome}: {m}" for m in a.avisos]
        if a.TIPO.startswith("Cozinha") and not a.tues:
            obs.append(f"🍳 {a.nome}: nenhuma TUE cadastrada. Geladeira, micro-ondas ou forno vão ficar onde?")
    r = resumo_quadro(circuitos)
    obs.append(f"🗄️ Deixe {r['reservas']} espaços reserva no quadro (6.5.4.7) e lembre do DPS na entrada.")
    if r["com_dr"]:
        obs.append(f"🛡️ {r['com_dr']} circuito(s) exigem DR 30 mA: dá pra usar 1 DR geral ou DRs por grupo.")
    obs.append("✅ Coordenação Ib ≤ In ≤ Iz conferida em todos os circuitos.")
    return obs


def resumo_quadro(circuitos: list[Circuito]) -> dict:
    return {
        "circuitos": len(circuitos),
        "reservas": espacos_reserva(len(circuitos)),
        "com_dr": sum(c.dr for c in circuitos),
        "maior_queda": max((c.queda_pct for c in circuitos), default=0.0),
    }

##############################################################################
#  CAMADA 5 · PERSISTÊNCIA
#  Salvar/abrir JSON, exportar CSV e memorial
##############################################################################
# Salvar/abrir projetos (JSON) e exportar relatórios (CSV e memorial HTML).

def salvar_projeto(projeto: Projeto, caminho: str | Path) -> None:
    caminho = Path(caminho)
    try:
        tmp = caminho.with_suffix(caminho.suffix + ".tmp")
        tmp.write_text(json.dumps(projeto.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(caminho)  # escrita atômica: nunca deixa o arquivo pela metade
    except OSError as e:
        raise PersistenciaErro(f"Não consegui salvar em {caminho}: {e}") from e


def abrir_projeto(caminho: str | Path) -> Projeto:
    try:
        dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
        return Projeto.from_dict(dados)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        raise PersistenciaErro(f"Arquivo de projeto inválido: {e}") from e


def _fmt(x: float, casas: int = 2) -> str:
    """Número no padrão BR (vírgula decimal) pro Excel brasileiro."""
    return f"{x:.{casas}f}".replace(".", ",")


def exportar_csv(projeto: Projeto, caminho: str | Path, circuitos: list[Circuito] | None = None) -> None:
    """CSV com ';' e BOM UTF-8: abre certinho no Excel em português."""
    circuitos = circuitos if circuitos is not None else montar_quadro(projeto)
    try:
        with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["QUADRO DE CARGAS", projeto.nome, f"{projeto.tensao} V"])
            w.writerow(["Nº", "Descrição", "Tipo", "Potência (VA)", "Tensão (V)", "Ib (A)",
                        "Disjuntor (A)", "Seção (mm²)", "Iz corrigida (A)", "Comprimento (m)",
                        "Queda (%)", "DR 30 mA", "Critério"])
            for c in circuitos:
                w.writerow([c.numero, c.descricao, c.tipo, _fmt(c.potencia_va, 0), c.tensao,
                            _fmt(c.corrente), c.disjuntor, _fmt(c.secao, 1), _fmt(c.iz_corrigida, 1),
                            _fmt(c.comprimento, 1), _fmt(c.queda_pct), "Sim" if c.dr else "Não",
                            c.criterio])
            w.writerow([])
            w.writerow(["PREVISÃO DE CARGAS POR AMBIENTE"])
            w.writerow(["Ambiente", "Tipo", "Área (m²)", "Perímetro (m)", "Iluminação (VA)",
                        "Qtd TUG", "TUG (VA)", "TUEs", "TUE (VA)", "Total (VA)"])
            for a in projeto.ambientes:
                w.writerow([a.nome, a.TIPO, _fmt(a.area), _fmt(a.perimetro), a.iluminacao_va,
                            a.qtd_tug, a.tug_va, ", ".join(t.nome for t in a.tues) or "-",
                            _fmt(a.tue_va, 0), _fmt(a.total_va, 0)])
            w.writerow([])
            w.writerow(["Total instalado (VA)", _fmt(projeto.total_va, 0)])
            w.writerow(["Total estimado (W)", _fmt(projeto.total_w, 0)])
    except OSError as e:
        raise PersistenciaErro(f"Não consegui exportar o CSV: {e}") from e


def gerar_memorial_html(projeto: Projeto, circuitos: list[Circuito] | None = None) -> str:
    """Memorial de cálculo em HTML (abre no navegador e dá pra imprimir em PDF)."""
    circuitos = circuitos if circuitos is not None else montar_quadro(projeto)
    r = resumo_quadro(circuitos)
    e = html.escape
    linhas_amb = "".join(
        f"<tr><td>{e(a.nome)}</td><td>{e(a.TIPO)}</td><td>{a.area:.2f}</td><td>{a.perimetro:.2f}</td>"
        f"<td>{a.iluminacao_va}</td><td>{e(a.memoria_tug())}</td><td>{a.tug_va}</td>"
        f"<td>{e(', '.join(f'{t.nome} ({t.potencia_w:.0f} W)' for t in a.tues) or '—')}</td>"
        f"<td><b>{a.total_va:.0f}</b></td></tr>" for a in projeto.ambientes)
    linhas_circ = "".join(
        f"<tr><td>{c.numero}</td><td>{e(c.descricao)}</td><td>{c.potencia_va:.0f}</td><td>{c.tensao}</td>"
        f"<td>{c.corrente:.2f}</td><td>{c.disjuntor}</td><td>{c.secao:g}</td><td>{c.iz_corrigida:.1f}</td>"
        f"<td>{c.queda_pct:.2f}%</td><td>{'✔' if c.dr else ''}</td><td>{e(c.criterio)}</td></tr>"
        for c in circuitos)
    avisos = [f"{a.nome}: {m}" for a in projeto.ambientes for m in a.avisos]
    bloco_avisos = ("<h2>⚠ Pontos de atenção</h2><ul>" + "".join(f"<li>{e(m)}</li>" for m in avisos)
                    + "</ul>") if avisos else ""
    fca = fator_agrupamento(projeto.circuitos_agrupados)
    fct = fator_temperatura(projeto.temperatura_ambiente)
    return f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<title>Memorial - {e(projeto.nome)}</title><style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:40px;color:#121212}}
h1{{color:#ff8c00;margin-bottom:0}} h2{{border-bottom:3px solid #ff8c00;padding-bottom:4px}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin-bottom:20px}}
th{{background:#121212;color:#ff8c00;padding:6px}} td{{border:1px solid #ddd;padding:5px;text-align:center}}
.kpi{{display:inline-block;background:#fff4e6;border-left:5px solid #ff8c00;padding:10px 18px;margin:6px}}
small{{color:#666}}</style></head><body>
<h1>⚡ Memorial de Cálculo Elétrico</h1>
<p><b>{e(projeto.nome)}</b> — gerado pelo ME Helper em {datetime.now():%d/%m/%Y %H:%M}</p>
<div class="kpi">Potência instalada<br><b>{projeto.total_va:,.0f} VA</b></div>
<div class="kpi">Potência estimada<br><b>{projeto.total_w:,.0f} W</b></div>
<div class="kpi">Circuitos<br><b>{r['circuitos']} + {r['reservas']} reservas</b></div>
<div class="kpi">Maior queda<br><b>{r['maior_queda']:.2f}%</b></div>
<h2>1. Premissas</h2><ul>
<li>NBR 5410:2004 — tensão F-N {projeto.tensao} V; condutores de cobre PVC 70 °C, método B1.</li>
<li>Fator de agrupamento: {projeto.circuitos_agrupados} circuito(s) → FCA = {fca}; temperatura {projeto.temperatura_ambiente} °C → FCT = {fct}.</li>
<li>Iluminação: 100 VA até 6 m² + 60 VA a cada 4 m² inteiros.</li>
<li>Coordenação: I<sub>b</sub> ≤ I<sub>n</sub> ≤ I<sub>z</sub>·FCA·FCT; queda de tensão ≤ {QUEDA_MAX_TERMINAL:g}% no circuito terminal.</li></ul>
<h2>2. Previsão de cargas</h2><table><tr><th>Ambiente</th><th>Tipo</th><th>Área (m²)</th><th>Perím. (m)</th>
<th>Ilum. (VA)</th><th>Regra TUG</th><th>TUG (VA)</th><th>TUEs</th><th>Total (VA)</th></tr>{linhas_amb}</table>
<h2>3. Quadro de circuitos</h2><table><tr><th>Nº</th><th>Circuito</th><th>VA</th><th>V</th><th>I<sub>b</sub> (A)</th>
<th>Disj. (A)</th><th>Seção (mm²)</th><th>I<sub>z</sub> corr. (A)</th><th>ΔV</th><th>DR</th><th>Critério</th></tr>{linhas_circ}</table>
{bloco_avisos}
<p><small>Documento de apoio acadêmico. Não substitui projeto assinado por profissional habilitado (ART/TRT).</small></p>
</body></html>"""


def exportar_memorial(projeto: Projeto, caminho: str | Path) -> None:
    try:
        Path(caminho).write_text(gerar_memorial_html(projeto), encoding="utf-8")
    except OSError as e:
        raise PersistenciaErro(f"Não consegui salvar o memorial: {e}") from e

##############################################################################
#  CAMADA 6 · INTELIGÊNCIA ARTIFICIAL
#  ProvedorIA (abstrata) -> Groq, Gemini, Ollama, Offline
##############################################################################
# Assistente de normas técnicas com IA gratuita.
#
# Arquitetura (polimorfismo de verdade):
#
#     ProvedorIA (abstrato)
#      ├── GroqProvedor     -> nuvem, grátis com chave (console.groq.com)
#      ├── GeminiProvedor   -> nuvem, grátis com chave (aistudio.google.com)
#      ├── OllamaProvedor   -> roda local no PC, 100% grátis e offline
#      └── OfflineProvedor  -> base de conhecimento embutida (sem internet)
#
# A interface gráfica só conhece ProvedorIA.responder(); trocar de IA é
# trocar o objeto. Só usa a biblioteca padrão (urllib), zero pip install.

PROMPT_SISTEMA = """Você é o assistente técnico do ME Helper, um app de dimensionamento \
elétrico residencial feito por um estudante de engenharia. Responda SEMPRE em português \
do Brasil, de forma direta e didática.

Especialidades: ABNT NBR 5410 (instalações de baixa tensão), NBR 5419 (SPDA), \
NBR ISO/CIE 8995-1 (iluminação de interiores), NR-10, NBR 14136 (plugues e tomadas), \
padrões de concessionárias (ex.: Enel, Light) e boas práticas de projeto.

Regras:
1. Só cite número de item/tabela da norma quando tiver segurança; se não tiver, diga \
"confira na norma" em vez de inventar.
2. Mostre fórmulas e contas quando houver cálculo.
3. Seja breve (até ~12 linhas) a menos que peçam detalhe.
4. Quando o assunto envolver segurança de pessoas, lembre que o projeto precisa de \
profissional habilitado com ART/TRT.
5. Se receber dados de um projeto, aponte erros e melhorias de forma objetiva."""

# Config portátil: fica do lado do app. Levou o app num pendrive? A chave vai junto.
# (Não suba esse arquivo pro GitHub!)
ARQ_CONFIG = Path(__file__).resolve().parent / "me_helper_config.json"


@dataclass
class ConfigIA:
    provedor: str = "Offline (sem internet)"
    groq_chave: str = ""
    groq_modelo: str = "auto"
    gemini_chave: str = ""
    gemini_modelo: str = "auto"
    ollama_url: str = "http://localhost:11434"
    ollama_modelo: str = "llama3.2"

    @classmethod
    def carregar(cls) -> "ConfigIA":
        cfg = cls()
        try:
            dados = json.loads(ARQ_CONFIG.read_text(encoding="utf-8"))
            for k, v in dados.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
        except (OSError, json.JSONDecodeError):
            pass
        # Variáveis de ambiente têm prioridade (jeito mais seguro de guardar chave)
        cfg.groq_chave = os.environ.get("GROQ_API_KEY", cfg.groq_chave)
        cfg.gemini_chave = os.environ.get("GEMINI_API_KEY", cfg.gemini_chave)
        return cfg

    def salvar(self) -> None:
        ARQ_CONFIG.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        try:
            os.chmod(ARQ_CONFIG, 0o600)  # só o dono lê (no Windows é ignorado)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# HTTP util
# ---------------------------------------------------------------------------
def _requisicao(url: str, corpo: dict | None = None, cabecalhos: dict | None = None,
                timeout: float = 60) -> dict:
    dados = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    req = urllib.request.Request(url, data=dados, method="POST" if dados else "GET",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "ME-Helper/5.0", **(cabecalhos or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode("utf-8", "ignore")[:300]
        mensagens = {
            400: "Requisição recusada (modelo ou parâmetro inválido).",
            401: "Chave de API inválida. Confira na aba Configurações.",
            403: "Acesso negado. A chave pode não ter permissão para esse modelo.",
            404: "Modelo não encontrado.",
            429: "Limite do plano gratuito atingido. Espera um minutinho e tenta de novo.",
        }
        texto = mensagens.get(e.code, f"Erro HTTP {e.code}.")
        if "API key not valid" in detalhe or "invalid_api_key" in detalhe:
            texto = mensagens[401]
        erro = IAErro(f"{texto}\n{detalhe}")
        erro.codigo = e.code  # type: ignore[attr-defined]
        raise erro from e
    except urllib.error.URLError as e:
        raise IAErro(f"Sem conexão com {url.split('/')[2]}: {e.reason}") from e
    except TimeoutError as e:
        raise IAErro("A IA demorou demais pra responder (timeout).") from e


Mensagem = dict  # {"role": "user"|"assistant", "content": str}


# ---------------------------------------------------------------------------
# Provedores
# ---------------------------------------------------------------------------
class ProvedorIA(ABC):
    nome: str = "abstrato"

    @abstractmethod
    def responder(self, historico: list[Mensagem]) -> str:
        """Recebe o histórico (última mensagem = pergunta) e devolve a resposta."""

    def __str__(self) -> str:
        return self.nome


class GroqProvedor(ProvedorIA):
    nome = "Groq (nuvem, grátis)"
    BASE = "https://api.groq.com/openai/v1"
    # Ordem de preferência; a Groq troca o catálogo com frequência,
    # então o app confere na API quais estão ativos de verdade.
    PREFERIDOS = ("openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b",
                  "meta-llama/llama-4-scout-17b-16e-instruct", "qwen/qwen3-32b", "llama-3.1-8b-instant")
    EXCLUIR = ("whisper", "tts", "guard", "playai", "orpheus", "compound", "prompt-guard")

    def __init__(self, chave: str, modelo: str = "auto") -> None:
        if not chave:
            raise IAErro("Cole sua chave gratuita da Groq em Configurações (console.groq.com/keys).")
        self.chave = chave
        self.modelo = None if modelo in ("", "auto") else modelo

    def _cab(self) -> dict:
        return {"Authorization": f"Bearer {self.chave}"}

    def _descobrir_modelo(self) -> str:
        r = _requisicao(f"{self.BASE}/models", cabecalhos=self._cab())
        ativos = [m["id"] for m in r.get("data", [])
                  if m.get("active", True) and not any(x in m["id"] for x in self.EXCLUIR)]
        for preferido in self.PREFERIDOS:
            if preferido in ativos:
                return preferido
        if not ativos:
            raise IAErro("Nenhum modelo de chat disponível na Groq para essa chave.")
        return sorted(ativos)[0]

    def _chamar(self, historico: list[Mensagem]) -> dict:
        corpo = {"model": self.modelo, "temperature": 0.3, "max_tokens": 2000,
                 "messages": [{"role": "system", "content": PROMPT_SISTEMA}, *historico]}
        if "gpt-oss" in self.modelo:
            corpo["reasoning_effort"] = "low"  # responde mais rápido e gasta menos cota
        return _requisicao(f"{self.BASE}/chat/completions", corpo, self._cab())

    def responder(self, historico: list[Mensagem]) -> str:
        if self.modelo is None:
            self.modelo = self._descobrir_modelo()
        try:
            r = self._chamar(historico)
        except IAErro as e:
            # 404 = modelo aposentado; 400 com model_decommissioned também
            if getattr(e, "codigo", None) in (400, 404) and "model" in str(e).lower():
                self.modelo = self._descobrir_modelo()
                r = self._chamar(historico)
            else:
                raise
        try:
            return (r["choices"][0]["message"].get("content") or "").strip()
        except (KeyError, IndexError) as e:
            raise IAErro(f"Resposta inesperada da Groq: {r}") from e


class GeminiProvedor(ProvedorIA):
    nome = "Google Gemini (nuvem, grátis)"
    BASE = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, chave: str, modelo: str = "auto") -> None:
        if not chave:
            raise IAErro("Cole sua chave gratuita do Gemini em Configurações (aistudio.google.com/apikey).")
        self.chave = chave
        self.modelo = None if modelo in ("", "auto") else modelo

    def _descobrir_modelo(self) -> str:
        """Os nomes dos modelos mudam toda hora; pergunta pra API qual 'flash' existe."""
        r = _requisicao(f"{self.BASE}/models?pageSize=200", cabecalhos={"x-goog-api-key": self.chave})
        candidatos = [
            m["name"].removeprefix("models/") for m in r.get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
            and "flash" in m["name"]
            and not any(x in m["name"] for x in ("image", "tts", "audio", "live", "exp", "embedding"))
        ]
        if not candidatos:
            raise IAErro("Nenhum modelo Gemini 'flash' disponível para essa chave.")
        # Prefere versões estáveis (sem 'preview') e a mais nova
        candidatos.sort(key=lambda n: ("preview" not in n, n), reverse=True)
        return candidatos[0]

    def responder(self, historico: list[Mensagem]) -> str:
        if self.modelo is None:
            self.modelo = self._descobrir_modelo()
        contents = [{"role": "model" if m["role"] == "assistant" else "user",
                     "parts": [{"text": m["content"]}]} for m in historico]
        corpo = {"systemInstruction": {"parts": [{"text": PROMPT_SISTEMA}]},
                 "contents": contents,
                 "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1200}}
        url = f"{self.BASE}/models/{self.modelo}:generateContent"
        try:
            r = _requisicao(url, corpo, {"x-goog-api-key": self.chave})
        except IAErro as e:
            if getattr(e, "codigo", None) == 404:  # modelo aposentado -> descobre outro
                self.modelo = self._descobrir_modelo()
                r = _requisicao(f"{self.BASE}/models/{self.modelo}:generateContent",
                                corpo, {"x-goog-api-key": self.chave})
            else:
                raise
        try:
            return "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"]).strip()
        except (KeyError, IndexError) as e:
            raise IAErro(f"Resposta bloqueada ou vazia do Gemini: {str(r)[:300]}") from e


class OllamaProvedor(ProvedorIA):
    nome = "Ollama (local, offline)"

    def __init__(self, url: str, modelo: str) -> None:
        self.url, self.modelo = url.rstrip("/"), modelo

    def responder(self, historico: list[Mensagem]) -> str:
        corpo = {"model": self.modelo, "stream": False,
                 "messages": [{"role": "system", "content": PROMPT_SISTEMA}, *historico]}
        try:
            r = _requisicao(f"{self.url}/api/chat", corpo, timeout=180)
        except IAErro as e:
            raise IAErro(f"{e}\nO Ollama está aberto? Rode: ollama pull {self.modelo}") from e
        return r.get("message", {}).get("content", "").strip()


def _normalizar(txt: str) -> str:
    sem_acento = unicodedata.normalize("NFD", txt.lower())
    return "".join(c for c in sem_acento if unicodedata.category(c) != "Mn")


class OfflineProvedor(ProvedorIA):
    """Plano B: responde as dúvidas mais comuns sem internet nem chave."""
    nome = "Offline (sem internet)"

    BASE: list[tuple[tuple[str, ...], str]] = [
        (("iluminacao", "luz", "lampada"),
         "💡 Iluminação (NBR 5410, 9.5.2.1): área ≤ 6 m² → mínimo 100 VA. Acima disso, "
         "100 VA nos primeiros 6 m² + 60 VA a cada 4 m² INTEIROS.\nEx.: 14 m² → 100 + 2×60 = 220 VA.\n"
         "Pelo menos 1 ponto de luz no teto por cômodo, comandado por interruptor."),
        (("tug", "tomada de uso geral", "quantas tomadas"),
         "🔌 TUG (9.5.2.2): salas/quartos 1 a cada 5 m de perímetro; cozinha, copa, A.S. e "
         "lavanderia 1 a cada 3,5 m; banheiro 1 junto ao lavatório; varanda ao menos 1; demais "
         "cômodos ≤ 6 m² ao menos 1.\nPotência: áreas molhadas 600 VA nas 3 primeiras e 100 VA "
         "nas demais; outros cômodos 100 VA por tomada."),
        (("tue", "uso especifico", "chuveiro", "ar condicionado", "forno"),
         "⚙️ TUE: potência nominal do equipamento, instalada a no máx. 1,5 m do local previsto. "
         "Equipamento com corrente > 10 A precisa de circuito independente (9.5.3.1). "
         "Ex.: chuveiro de 5500 W em 220 V → Ib = 25 A; disjuntor e cabo dependem do FCA (calcule na aba Quadro)."),
        (("dr", "diferencial", "residual"),
         "🛡️ DR de alta sensibilidade (≤ 30 mA, item 5.1.3.2.2) é obrigatório em: circuitos de "
         "banheiro, cozinha, copa, A.S., lavanderia, áreas externas e tomadas em áreas externas "
         "ou que possam alimentar equipamentos externos."),
        (("queda", "queda de tensao"),
         "📉 Queda de tensão: máx. 4% no circuito terminal (total 7% a partir do transformador "
         "próprio, 5% a partir do ponto de entrega).\nΔV% = 2·ρ·L·Ib / (S·V) × 100, com "
         "ρ ≈ 0,0172 Ω·mm²/m."),
        (("secao", "bitola", "cabo", "condutor", "fio"),
         "🧵 Seções mínimas de cobre: 1,5 mm² iluminação, 2,5 mm² força/tomadas. "
         "Iz (B1, 2 condutores, PVC): 1,5→17,5 A | 2,5→24 A | 4→32 A | 6→41 A | 10→57 A | "
         "16→76 A. Aplique FCA (agrupamento) e FCT (temperatura) antes de comparar com o disjuntor."),
        (("disjuntor", "coordenacao", "protecao"),
         "⚡ Coordenação: Ib ≤ In ≤ Iz (corrigida). Escolha o menor disjuntor comercial ≥ Ib e "
         "confira se o cabo aguenta o disjuntor (senão o cabo esquenta antes de desarmar)."),
        (("agrupamento", "fca"),
         "📦 FCA (Tabela 42, eletroduto): 1 circ → 1,00 | 2 → 0,80 | 3 → 0,70 | 4 → 0,65 | "
         "5 → 0,60 | 6 → 0,57. Iz_corrigida = Iz × FCA × FCT."),
        (("aterramento", "terra", "pe"),
         "🌍 Todo circuito deve ter condutor de proteção (PE). Esquema mais comum em residência: "
         "TN-S ou TT. Tomadas no padrão NBR 14136 têm o pino terra central."),
        (("reserva", "quadro", "qd"),
         "🗄️ Espaço reserva no quadro (6.5.4.7): até 6 circuitos → 2; 7 a 12 → 3; "
         "13 a 30 → 4; acima de 30 → 15%."),
        (("nr-10", "nr10", "seguranca"),
         "👷 NR-10: trabalho em instalações elétricas exige treinamento, desenergização "
         "(seccionamento, bloqueio, sinalização, teste de ausência de tensão e aterramento "
         "temporário) e uso de EPI/EPC adequados."),
    ]

    def responder(self, historico: list[Mensagem]) -> str:
        pergunta = _normalizar(historico[-1]["content"]) if historico else ""
        achados = [resp for chaves, resp in self.BASE if any(k in pergunta for k in chaves)]
        if achados:
            return "\n\n".join(achados[:2]) + "\n\n(modo offline 📴 — conecte uma IA em Configurações pra respostas completas)"
        return ("📴 Modo offline: não achei isso na base local. Tente palavras como "
                "'iluminação', 'TUG', 'TUE', 'DR', 'queda de tensão', 'cabo', 'disjuntor', "
                "'agrupamento', 'reserva' ou configure a Groq/Gemini (grátis) em Configurações.")


PROVEDORES = ["Groq (nuvem, grátis)", "Google Gemini (nuvem, grátis)",
              "Ollama (local, offline)", "Offline (sem internet)"]


def criar_provedor(cfg: ConfigIA) -> ProvedorIA:
    if cfg.provedor.startswith("Groq"):
        return GroqProvedor(cfg.groq_chave, cfg.groq_modelo)
    if cfg.provedor.startswith("Google"):
        return GeminiProvedor(cfg.gemini_chave, cfg.gemini_modelo)
    if cfg.provedor.startswith("Ollama"):
        return OllamaProvedor(cfg.ollama_url, cfg.ollama_modelo)
    return OfflineProvedor()


class Assistente:
    """Guarda o histórico da conversa e limita o tamanho (economiza tokens grátis)."""
    MAX_MENSAGENS = 12

    def __init__(self, provedor: ProvedorIA) -> None:
        self.provedor = provedor
        self.historico: list[Mensagem] = []

    def perguntar(self, texto: str) -> str:
        self.historico.append({"role": "user", "content": texto})
        self.historico = self.historico[-self.MAX_MENSAGENS:]
        while self.historico and self.historico[0]["role"] != "user":
            self.historico.pop(0)  # a conversa precisa começar pelo usuário
        try:
            resposta = self.provedor.responder(self.historico)
        except IAErro:
            self.historico.pop()  # não deixa pergunta órfã no histórico
            raise
        self.historico.append({"role": "assistant", "content": resposta})
        return resposta

    def limpar(self) -> None:
        self.historico.clear()

##############################################################################
#  CAMADA 7 · INTERFACE GRÁFICA
#  Tkinter: só tela, nenhuma regra de negócio aqui
##############################################################################
# Interface gráfica (Tkinter) do ME Helper v5.
#
# A GUI só cuida de tela: toda regra de negócio está em modelos/calculos/ia.
# Isso é separação de responsabilidades (padrão parecido com MVC).

# ---------------------------------------------------------------- paleta
BG, BG2, BG3 = "#121212", "#1a1a1a", "#2e2e2e"
LARANJA, BRANCO, CINZA = "#ff8c00", "#ffffff", "#a6a6a6"
VERMELHO, AZUL, VERDE = "#e74c3c", "#3498db", "#2ecc71"
FONTE = "Segoe UI"


class ToolTip:
    """Dica flutuante ao passar o mouse (posição pelo próprio widget)."""

    def __init__(self, widget: tk.Widget, texto: str) -> None:
        self.widget, self.texto, self.janela = widget, texto, None
        widget.bind("<Enter>", self.mostrar, add="+")
        widget.bind("<Leave>", self.esconder, add="+")

    def mostrar(self, _=None) -> None:
        if self.janela:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.janela = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(tw, text=self.texto, justify="left", bg=LARANJA, fg=BG, wraplength=320,
                 font=(FONTE, 9), padx=6, pady=3).pack()

    def esconder(self, _=None) -> None:
        if self.janela:
            self.janela.destroy()
            self.janela = None


def _num(texto: str, campo: str) -> float:
    """Converte aceitando vírgula brasileira ("12,5")."""
    try:
        return float(texto.strip().replace(",", "."))
    except ValueError:
        raise MEHelperErro(f"'{campo}' precisa ser um número (ex.: 12,5).") from None


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.projeto = Projeto()
        self.arquivo: Path | None = None
        self.alterado = False
        self.editando_id: str | None = None
        self.tues_temp: list[TUE] = []
        self.circuitos: list[Circuito] = []

        self.cfg = ConfigIA.carregar()
        self.assistente = Assistente(OfflineProvedor())
        self._fila_ia: queue.Queue = queue.Queue()
        self._ocupado_ia = False

        root.title(f"ME Helper v{__version__}")
        root.geometry("1150x820")
        root.minsize(980, 700)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.sair)

        self._estilos()
        self._menu()
        tk.Label(root, text="⚡ MY ELECTRIC HELPER", font=(FONTE, 22, "bold"), bg=BG, fg=LARANJA).pack(pady=(14, 0))
        tk.Label(root, text="Dimensionamento residencial pela NBR 5410 + assistente de normas com IA",
                 font=(FONTE, 10), bg=BG, fg=CINZA).pack(pady=(0, 8))

        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=True, padx=16, pady=(0, 6))
        self.aba_amb, self.aba_proj, self.aba_qd, self.aba_ia, self.aba_cfg = (
            tk.Frame(self.nb, bg=BG) for _ in range(5))
        for aba, titulo in [(self.aba_amb, " ⚡ AMBIENTE "), (self.aba_proj, " 📋 PROJETO "),
                            (self.aba_qd, " 🗄️ QUADRO "), (self.aba_ia, " 🤖 ASSISTENTE IA "),
                            (self.aba_cfg, " ⚙️ CONFIGURAÇÕES ")]:
            self.nb.add(aba, text=titulo)

        self.status = tk.Label(root, text="Pronto.", anchor="w", bg=BG2, fg=CINZA, font=(FONTE, 9), padx=10)
        self.status.pack(fill="x", side="bottom")

        self._aba_ambiente()
        self._aba_projeto()
        self._aba_quadro()
        self._aba_ia()
        self._aba_config()
        self._aplicar_provedor(silencioso=True)
        self.atualizar_tudo()

    # =============================================================== estilo
    def _estilos(self) -> None:
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("TNotebook", background=BG, borderwidth=0)
        s.configure("TNotebook.Tab", background=BG3, foreground=BRANCO, padding=[14, 6], font=(FONTE, 10, "bold"))
        s.map("TNotebook.Tab", background=[("selected", LARANJA)], foreground=[("selected", BG)])
        s.configure("Treeview", background=BG2, fieldbackground=BG2, foreground=BRANCO, rowheight=26,
                    font=(FONTE, 9), borderwidth=0)
        s.configure("Treeview.Heading", background=BG3, foreground=LARANJA, font=(FONTE, 9, "bold"))
        s.map("Treeview", background=[("selected", LARANJA)], foreground=[("selected", BG)])
        s.configure("TCombobox", fieldbackground=BG3, background=BG3, foreground=BRANCO, arrowcolor=LARANJA)
        s.map("TCombobox", fieldbackground=[("readonly", BG3)], foreground=[("readonly", BRANCO)])
        self.root.option_add("*TCombobox*Listbox.background", BG3)
        self.root.option_add("*TCombobox*Listbox.foreground", BRANCO)

    def _menu(self) -> None:
        m = tk.Menu(self.root)
        arq = tk.Menu(m, tearoff=0)
        arq.add_command(label="Novo projeto", command=self.novo, accelerator="Ctrl+N")
        arq.add_command(label="Abrir...", command=self.abrir, accelerator="Ctrl+O")
        arq.add_command(label="Salvar", command=self.salvar, accelerator="Ctrl+S")
        arq.add_command(label="Salvar como...", command=lambda: self.salvar(como=True))
        arq.add_separator()
        arq.add_command(label="Exportar CSV (Excel)", command=self.exportar_csv)
        arq.add_command(label="Exportar memorial (HTML/PDF)", command=self.exportar_memorial)
        arq.add_separator()
        arq.add_command(label="Sair", command=self.sair)
        m.add_cascade(label="Arquivo", menu=arq)
        self.root.config(menu=m)
        self.root.bind("<Control-n>", lambda e: self.novo())
        self.root.bind("<Control-o>", lambda e: self.abrir())
        self.root.bind("<Control-s>", lambda e: self.salvar())

    # ---------------------------------------------------------- helpers UI
    def _label(self, pai, texto, **kw):
        return tk.Label(pai, text=texto, bg=kw.pop("bg", BG), fg=kw.pop("fg", BRANCO),
                        font=kw.pop("font", (FONTE, 10, "bold")), **kw)

    def _entry(self, pai, largura=30, **kw):
        return tk.Entry(pai, bg=BG3, fg=BRANCO, insertbackground=LARANJA, relief="flat",
                        width=largura, font=(FONTE, 10), **kw)

    def _botao(self, pai, texto, cmd, cor=LARANJA, fg=BG, **kw):
        return tk.Button(pai, text=texto, command=cmd, bg=cor, fg=fg, activebackground=BRANCO,
                         relief="flat", font=(FONTE, kw.pop("tam", 9), "bold"), cursor="hand2",
                         padx=kw.pop("padx", 12), pady=kw.pop("pady", 5), **kw)

    def _set_status(self, txt: str) -> None:
        self.status.config(text=txt)

    # ======================================================= aba AMBIENTE
    def _aba_ambiente(self) -> None:
        wrap = tk.Frame(self.aba_amb, bg=BG)
        wrap.pack(fill="both", expand=True, padx=20, pady=12)
        form = tk.Frame(wrap, bg=BG)
        form.pack(side="left", fill="y", anchor="n")

        self.lbl_modo = self._label(form, "NOVO AMBIENTE", fg=LARANJA, font=(FONTE, 12, "bold"))
        self.lbl_modo.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        self.v_nome, self.v_area, self.v_perim = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.v_dist, self.v_extras = tk.StringVar(value="10"), tk.StringVar(value="0")
        self.v_tipo = tk.StringVar(value=tipos_disponiveis()[0])

        campos = [
            ("Nome do cômodo:", self.v_nome, None),
            ("Área (m²):", self.v_area, "Área interna. Define a carga mínima de iluminação: 100 VA até 6 m² + 60 VA a cada 4 m² inteiros."),
            ("Perímetro (m):", self.v_perim, "Soma das paredes. Define a quantidade mínima de TUGs. Aceita vírgula (12,5)."),
            ("Distância ao QD (m):", self.v_dist, "Comprimento estimado do circuito até o quadro. Usado na queda de tensão."),
            ("TUGs extras:", self.v_extras, "Tomadas além do mínimo da norma (ex.: bancada do home office)."),
        ]
        for i, (txt, var, dica) in enumerate(campos, start=1):
            lbl = self._label(form, txt)
            lbl.grid(row=i, column=0, sticky="w", pady=5)
            self._entry(form, textvariable=var).grid(row=i, column=1, sticky="ew", padx=8)
            if dica:
                ToolTip(lbl, dica)
            var.trace_add("write", lambda *_: self._preview())

        self._label(form, "Tipo de ambiente:").grid(row=6, column=0, sticky="w", pady=5)
        cb = ttk.Combobox(form, textvariable=self.v_tipo, values=tipos_disponiveis(), state="readonly", width=38)
        cb.grid(row=6, column=1, sticky="ew", padx=8)
        cb.bind("<<ComboboxSelected>>", lambda e: self._preview())

        # --- TUEs
        ftue = tk.LabelFrame(form, text=" Tomadas de Uso Específico (TUE) ", bg=BG, fg=LARANJA,
                             font=(FONTE, 10, "bold"), padx=10, pady=8, bd=1)
        ftue.grid(row=7, column=0, columnspan=2, sticky="ew", pady=12)
        self.v_tue_nome, self.v_tue_pot = tk.StringVar(), tk.StringVar()
        self.v_tue_v, self.v_tue_fp = tk.StringVar(value="127"), tk.StringVar(value="1,0")
        linha = tk.Frame(ftue, bg=BG)
        linha.pack(fill="x")
        for txt, var, larg in [("Equip.:", self.v_tue_nome, 14), ("W:", self.v_tue_pot, 7), ("FP:", self.v_tue_fp, 4)]:
            self._label(linha, txt, font=(FONTE, 9)).pack(side="left")
            self._entry(linha, larg, textvariable=var).pack(side="left", padx=(2, 8))
        ttk.Combobox(linha, textvariable=self.v_tue_v, values=["127", "220"], width=4,
                     state="readonly").pack(side="left", padx=(0, 6))
        self._botao(linha, "+", self.adicionar_tue, padx=8).pack(side="left")
        lst_frame = tk.Frame(ftue, bg=BG)
        lst_frame.pack(fill="x", pady=(8, 0))
        self.lst_tue = tk.Listbox(lst_frame, bg=BG2, fg=BRANCO, height=4, font=(FONTE, 9),
                                  highlightthickness=0, selectbackground=LARANJA, relief="flat")
        self.lst_tue.pack(side="left", fill="x", expand=True)
        self._botao(lst_frame, "REMOVER", self.remover_tue, cor=VERMELHO, fg=BRANCO).pack(side="left", padx=6)

        botoes = tk.Frame(form, bg=BG)
        botoes.grid(row=8, column=0, columnspan=2, pady=8)
        self.btn_salvar_amb = self._botao(botoes, "SALVAR AMBIENTE NO PROJETO", self.salvar_ambiente,
                                          tam=11, padx=20, pady=10)
        self.btn_salvar_amb.pack(side="left")
        self.btn_cancelar = self._botao(botoes, "CANCELAR EDIÇÃO", self.cancelar_edicao, cor=BG3, fg=BRANCO,
                                        pady=10)
        form.columnconfigure(1, weight=1)

        # --- preview ao vivo
        prev = tk.LabelFrame(wrap, text=" PRÉVIA AO VIVO ", bg=BG2, fg=LARANJA, font=(FONTE, 10, "bold"),
                             padx=16, pady=12, bd=1)
        prev.pack(side="left", fill="both", expand=True, padx=(24, 0), anchor="n")
        self.lbl_preview = tk.Label(prev, text="", bg=BG2, fg=BRANCO, justify="left", anchor="nw",
                                    font=("Consolas", 10), wraplength=430)
        self.lbl_preview.pack(fill="both", expand=True)

    def _ler_formulario(self):
        """Lê os campos e devolve um Ambiente validado (ou levanta erro amigável)."""
        extras = _num(self.v_extras.get() or "0", "TUGs extras")
        if extras != int(extras):
            raise MEHelperErro("TUGs extras precisa ser um número inteiro.")
        return criar_ambiente(
            self.v_tipo.get(), nome=self.v_nome.get(), area=_num(self.v_area.get(), "Área"),
            perimetro=_num(self.v_perim.get(), "Perímetro"), distancia_qd=_num(self.v_dist.get(), "Distância"),
            tug_extras=int(extras), tues=list(self.tues_temp))

    def _preview(self) -> None:
        if not (self.v_area.get().strip() and self.v_perim.get().strip()):
            self.lbl_preview.config(text="Preencha área e perímetro pra ver o cálculo em tempo real ✨", fg=CINZA)
            return
        try:
            a = criar_ambiente(self.v_tipo.get(), nome=self.v_nome.get() or "(sem nome)",
                               area=_num(self.v_area.get(), "Área"), perimetro=_num(self.v_perim.get(), "Perímetro"),
                               distancia_qd=_num(self.v_dist.get() or "10", "Distância"),
                               tug_extras=int(_num(self.v_extras.get() or "0", "TUGs extras")),
                               tues=list(self.tues_temp))
        except MEHelperErro as e:
            self.lbl_preview.config(text=f"⚠ {e}", fg=VERMELHO)
            return
        txt = [f"💡 Iluminação: {a.iluminacao_va} VA",
               f"🔌 TUGs: {a.memoria_tug()}",
               f"    → {a.qtd_tug} tomada(s), {a.tug_va} VA  ({', '.join(map(str, a.potencias_tug()))})"]
        for t in a.tues:
            txt.append(f"⚙️ TUE {t.nome}: {t.potencia_va:.0f} VA em {t.tensao} V → Ib = {t.corrente:.1f} A")
        txt.append(f"\nΣ TOTAL DO CÔMODO: {a.total_va:.0f} VA")
        if a.PRECISA_DR:
            txt.append("🛡️ Circuitos deste cômodo exigem DR 30 mA")
        if a.CIRCUITO_TUG_EXCLUSIVO:
            txt.append("🔒 TUGs em circuito exclusivo (9.5.3.3)")
        txt += [f"⚠ {m}" for m in a.avisos]
        self.lbl_preview.config(text="\n".join(txt), fg=BRANCO)

    def adicionar_tue(self) -> None:
        try:
            tue = TUE(self.v_tue_nome.get(), _num(self.v_tue_pot.get(), "Potência"),
                      int(self.v_tue_v.get()), _num(self.v_tue_fp.get() or "1", "FP"))
        except MEHelperErro as e:
            messagebox.showerror("TUE inválida", str(e))
            return
        self.tues_temp.append(tue)
        self.lst_tue.insert(tk.END, f"{tue.nome} — {tue.potencia_w:.0f} W / {tue.tensao} V  (Ib {tue.corrente:.1f} A)")
        self.v_tue_nome.set("")
        self.v_tue_pot.set("")
        self._preview()

    def remover_tue(self) -> None:
        sel = self.lst_tue.curselection()
        if sel:
            self.lst_tue.delete(sel[0])
            self.tues_temp.pop(sel[0])
            self._preview()

    def salvar_ambiente(self) -> None:
        try:
            amb = self._ler_formulario()
            if self.editando_id:
                self.projeto.substituir(self.editando_id, amb)
                msg = f"'{amb.nome}' atualizado."
            else:
                self.projeto.adicionar(amb)
                msg = f"'{amb.nome}' adicionado ao projeto."
        except MEHelperErro as e:
            messagebox.showerror("Confere aí", str(e))
            return
        self._marcar_alterado()
        self.limpar_formulario()
        self.atualizar_tudo()
        self._set_status("✔ " + msg)

    def limpar_formulario(self) -> None:
        for v in (self.v_nome, self.v_area, self.v_perim, self.v_tue_nome, self.v_tue_pot):
            v.set("")
        self.v_dist.set("10")
        self.v_extras.set("0")
        self.tues_temp.clear()
        self.lst_tue.delete(0, tk.END)
        self.editando_id = None
        self.lbl_modo.config(text="NOVO AMBIENTE", fg=LARANJA)
        self.btn_salvar_amb.config(text="SALVAR AMBIENTE NO PROJETO")
        self.btn_cancelar.pack_forget()
        self._preview()

    def cancelar_edicao(self) -> None:
        self.limpar_formulario()
        self._set_status("Edição cancelada — nada foi alterado.")

    # ======================================================== aba PROJETO
    def _aba_projeto(self) -> None:
        topo = tk.Frame(self.aba_proj, bg=BG)
        topo.pack(fill="x", padx=20, pady=(12, 4))
        self._label(topo, "Nome do projeto:").pack(side="left")
        self.v_proj_nome = tk.StringVar(value=self.projeto.nome)
        e = self._entry(topo, 40, textvariable=self.v_proj_nome)
        e.pack(side="left", padx=8)
        e.bind("<FocusOut>", lambda _: self._renomear_projeto())
        e.bind("<Return>", lambda _: self._renomear_projeto())

        cols = ("tipo", "area", "perim", "ilum", "tug", "tue", "total", "dr")
        self.tree_amb = ttk.Treeview(self.aba_proj, columns=cols, show="tree headings", height=12)
        self.tree_amb.heading("#0", text="Ambiente")
        self.tree_amb.column("#0", width=150)
        for c, t, w in zip(cols, ["Tipo", "Área m²", "Perím. m", "Ilum. VA", "TUGs", "TUE VA", "Total VA", "DR"],
                           [230, 70, 70, 75, 110, 75, 85, 45]):
            self.tree_amb.heading(c, text=t)
            self.tree_amb.column(c, width=w, anchor="center")
        self.tree_amb.pack(fill="both", expand=True, padx=20, pady=6)
        self.tree_amb.bind("<Double-1>", lambda e: self.editar_selecionado())
        self.tree_amb.tag_configure("aviso", foreground=LARANJA)

        bts = tk.Frame(self.aba_proj, bg=BG)
        bts.pack(pady=4)
        self._botao(bts, "✏️ EDITAR", self.editar_selecionado, cor=AZUL, fg=BRANCO).pack(side="left", padx=5)
        self._botao(bts, "🗑 REMOVER", self.remover_selecionado, cor=VERMELHO, fg=BRANCO).pack(side="left", padx=5)
        self._botao(bts, "🤖 REVISAR COM IA", self.revisar_com_ia, cor=BG3, fg=LARANJA).pack(side="left", padx=5)

        res = tk.LabelFrame(self.aba_proj, text=" RESUMO DA EDIFICAÇÃO ", bg=BG2, fg=LARANJA,
                            font=(FONTE, 10, "bold"), padx=15, pady=10, bd=1)
        res.pack(fill="x", padx=20, pady=10)
        self.lbl_resumo = tk.Label(res, text="", bg=BG2, fg=BRANCO, justify="left", font=(FONTE, 10))
        self.lbl_resumo.pack(anchor="w")

    def _renomear_projeto(self) -> None:
        novo = self.v_proj_nome.get().strip() or "Projeto sem nome"
        if novo != self.projeto.nome:
            self.projeto.nome = novo
            self._marcar_alterado()

    def _selecionado(self):
        sel = self.tree_amb.selection()
        if not sel:
            messagebox.showinfo("Seleciona um", "Clica em um cômodo da lista primeiro.")
            return None
        return next((a for a in self.projeto.ambientes if a.id == sel[0]), None)

    def editar_selecionado(self) -> None:
        amb = self._selecionado()
        if not amb:
            return
        self.limpar_formulario()
        self.editando_id = amb.id
        self.v_nome.set(amb.nome)
        self.v_tipo.set(amb.TIPO)
        self.v_area.set(f"{amb.area:g}")
        self.v_perim.set(f"{amb.perimetro:g}")
        self.v_dist.set(f"{amb.distancia_qd:g}")
        self.v_extras.set(str(amb.tug_extras))
        for t in amb.tues:
            self.tues_temp.append(t)
            self.lst_tue.insert(tk.END, f"{t.nome} — {t.potencia_w:.0f} W / {t.tensao} V  (Ib {t.corrente:.1f} A)")
        self.lbl_modo.config(text=f"EDITANDO: {amb.nome.upper()}", fg=AZUL)
        self.btn_salvar_amb.config(text="SALVAR ALTERAÇÕES")
        self.btn_cancelar.pack(side="left", padx=8)
        self._preview()
        self.nb.select(self.aba_amb)

    def remover_selecionado(self) -> None:
        amb = self._selecionado()
        if amb and messagebox.askyesno("Remover", f"Remover '{amb.nome}' do projeto?"):
            self.projeto.remover(amb.id)
            if self.editando_id == amb.id:
                self.limpar_formulario()
            self._marcar_alterado()
            self.atualizar_tudo()

    # ========================================================= aba QUADRO
    def _aba_quadro(self) -> None:
        cfg = tk.Frame(self.aba_qd, bg=BG)
        cfg.pack(fill="x", padx=20, pady=(12, 4))
        self.v_tensao = tk.StringVar(value=str(self.projeto.tensao))
        self.v_agrup = tk.StringVar(value=str(self.projeto.circuitos_agrupados))
        self.v_temp = tk.StringVar(value=str(self.projeto.temperatura_ambiente))
        itens = [("Tensão F-N (V):", self.v_tensao, ["127", "220"],
                  "Tensão de iluminação e TUGs. As TUEs usam a tensão de cada equipamento."),
                 ("Circuitos no mesmo eletroduto:", self.v_agrup, [str(k) for k in FCA_AGRUPAMENTO],
                  "Pior trecho de agrupamento. Define o FCA (Tabela 42). Mais circuitos juntos = cabo mais grosso."),
                 ("Temp. ambiente (°C):", self.v_temp, [str(k) for k in FCT_TEMPERATURA_PVC],
                  "Define o FCT (Tabela 40). Padrão da norma: 30 °C.")]
        for txt, var, vals, dica in itens:
            lbl = self._label(cfg, txt)
            lbl.pack(side="left")
            ToolTip(lbl, dica)
            cb = ttk.Combobox(cfg, textvariable=var, values=vals, width=5, state="readonly")
            cb.pack(side="left", padx=(4, 18))
            cb.bind("<<ComboboxSelected>>", lambda e: self._mudar_parametros())

        cols = ("desc", "va", "v", "ib", "disj", "secao", "iz", "dv", "dr", "crit")
        self.tree_qd = ttk.Treeview(self.aba_qd, columns=cols, show="headings", height=14)
        for c, t, w in zip(cols, ["Circuito", "VA", "V", "Ib (A)", "Disj. (A)", "Cabo mm²", "Iz corr. (A)",
                                  "Queda %", "DR", "Critério do cabo"],
                           [310, 65, 45, 60, 65, 70, 80, 65, 40, 150]):
            self.tree_qd.heading(c, text=t)
            self.tree_qd.column(c, width=w, anchor="w" if c == "desc" else "center")
        self.tree_qd.tag_configure("queda", foreground=LARANJA)
        self.tree_qd.tag_configure("tue", foreground="#9fd3ff")
        self.tree_qd.pack(fill="both", expand=True, padx=20, pady=6)

        self.lbl_qd = tk.Label(self.aba_qd, text="", bg=BG, fg=BRANCO, font=(FONTE, 10), justify="left")
        self.lbl_qd.pack(anchor="w", padx=22)
        bts = tk.Frame(self.aba_qd, bg=BG)
        bts.pack(pady=8)
        self._botao(bts, "📊 EXPORTAR CSV", self.exportar_csv, cor=BG3, fg=LARANJA).pack(side="left", padx=5)
        self._botao(bts, "📄 MEMORIAL DE CÁLCULO", self.exportar_memorial).pack(side="left", padx=5)

    def _mudar_parametros(self) -> None:
        self.projeto.tensao = int(self.v_tensao.get())
        self.projeto.circuitos_agrupados = int(self.v_agrup.get())
        self.projeto.temperatura_ambiente = int(self.v_temp.get())
        self._marcar_alterado()
        self.atualizar_tudo()

    # ============================================================ aba IA
    def _aba_ia(self) -> None:
        topo = tk.Frame(self.aba_ia, bg=BG)
        topo.pack(fill="x", padx=20, pady=(10, 4))
        self.lbl_prov = self._label(topo, "", fg=CINZA, font=(FONTE, 9))
        self.lbl_prov.pack(side="left")
        self._botao(topo, "🧹 Nova conversa", self.nova_conversa, cor=BG3, fg=BRANCO).pack(side="right")

        corpo = tk.Frame(self.aba_ia, bg=BG)
        corpo.pack(fill="both", expand=True, padx=20)
        self.chat = tk.Text(corpo, bg=BG2, fg=BRANCO, wrap="word", font=(FONTE, 10), relief="flat",
                            padx=12, pady=10, state="disabled", insertbackground=LARANJA)
        sb = tk.Scrollbar(corpo, command=self.chat.yview)
        self.chat.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.chat.pack(side="left", fill="both", expand=True)
        self.chat.tag_configure("voce", foreground=LARANJA, font=(FONTE, 10, "bold"), spacing1=10)
        self.chat.tag_configure("ia", foreground="#9fd3ff", font=(FONTE, 10, "bold"), spacing1=10)
        self.chat.tag_configure("erro", foreground=VERMELHO)
        self.chat.tag_configure("info", foreground=CINZA, font=(FONTE, 9, "italic"))

        chips = tk.Frame(self.aba_ia, bg=BG)
        chips.pack(fill="x", padx=20, pady=(6, 0))
        for txt in ["Quando o DR é obrigatório?", "Como calcular queda de tensão?",
                    "Chuveiro em 127 ou 220 V?", "Explique o fator de agrupamento"]:
            self._botao(chips, txt, lambda t=txt: self.enviar(t), cor=BG3, fg=BRANCO, tam=8,
                        pady=3).pack(side="left", padx=(0, 6))
        self._botao(chips, "🤖 Revisar meu projeto", self.revisar_com_ia, tam=8, pady=3).pack(side="left")

        base = tk.Frame(self.aba_ia, bg=BG)
        base.pack(fill="x", padx=20, pady=10)
        self.v_pergunta = tk.StringVar()
        ent = self._entry(base, 80, textvariable=self.v_pergunta)
        ent.pack(side="left", fill="x", expand=True, ipady=6)
        ent.bind("<Return>", lambda e: self.enviar())
        self.btn_enviar = self._botao(base, "ENVIAR ➤", self.enviar, padx=18, pady=7)
        self.btn_enviar.pack(side="left", padx=(8, 0))
        self._chat_add("info", "Pergunte sobre NBR 5410, NR-10, DR, queda de tensão, dimensionamento...\n"
                               "Dica: configure a Groq ou o Gemini (grátis) na aba Configurações.\n")

    def _chat_add(self, tag: str, texto: str) -> None:
        self.chat.config(state="normal")
        rotulo = {"voce": "Você:\n", "ia": "Assistente:\n"}.get(tag, "")
        if rotulo:
            self.chat.insert(tk.END, rotulo, tag)
            self.chat.insert(tk.END, texto + "\n")
        else:
            self.chat.insert(tk.END, texto + "\n", tag)
        self.chat.config(state="disabled")
        self.chat.see(tk.END)

    def enviar(self, texto: str | None = None, mostrar: str | None = None) -> None:
        pergunta = (texto if texto is not None else self.v_pergunta.get()).strip()
        if not pergunta or self._ocupado_ia:
            return
        self.v_pergunta.set("")
        self._chat_add("voce", mostrar or pergunta)
        self._ocupado_ia = True
        self.btn_enviar.config(state="disabled", text="pensando...")
        self.nb.select(self.aba_ia)

        def trabalho():  # roda em outra thread: a janela não congela
            try:
                self._fila_ia.put(("ia", self.assistente.perguntar(pergunta)))
            except IAErro as e:
                self._fila_ia.put(("erro", f"⚠ {e}"))
            except Exception as e:  # noqa: BLE001 - nunca deixar a thread morrer calada
                self._fila_ia.put(("erro", f"⚠ Erro inesperado: {e}"))

        threading.Thread(target=trabalho, daemon=True).start()
        self.root.after(100, self._checar_fila)

    def _checar_fila(self) -> None:
        try:
            tag, txt = self._fila_ia.get_nowait()
        except queue.Empty:
            self.root.after(100, self._checar_fila)
            return
        self._chat_add(tag, txt)
        self._ocupado_ia = False
        self.btn_enviar.config(state="normal", text="ENVIAR ➤")

    def revisar_com_ia(self) -> None:
        if not self.projeto.ambientes:
            messagebox.showinfo("Projeto vazio", "Adicione pelo menos um cômodo antes.")
            return
        if isinstance(self.assistente.provedor, OfflineProvedor):
            self.nb.select(self.aba_ia)
            self._chat_add("voce", "🤖 Revise meu projeto.")
            self._chat_add("ia", "Revisão local por regras (modo offline):\n• " +
                           "\n• ".join(revisao_local(self.projeto, self.circuitos)))
            return
        self.enviar(self._contexto_projeto(), mostrar="🤖 Revise meu projeto (dados enviados automaticamente).")

    def _contexto_projeto(self) -> str:
        p = self.projeto
        linhas = [f"Revise este projeto elétrico residencial ({p.tensao} V F-N, "
                  f"{p.circuitos_agrupados} circuitos por eletroduto, {p.temperatura_ambiente} °C). "
                  "Aponte erros, riscos e melhorias à luz da NBR 5410.", "", "AMBIENTES:"]
        for a in p.ambientes:
            tues = "; ".join(f"{t.nome} {t.potencia_w:.0f} W {t.tensao} V" for t in a.tues) or "nenhuma"
            linhas.append(f"- {a.nome} ({a.TIPO}): {a.area} m², {a.perimetro} m, ilum {a.iluminacao_va} VA, "
                          f"{a.qtd_tug} TUG = {a.tug_va} VA, TUEs: {tues}")
        linhas.append("\nCIRCUITOS:")
        for c in self.circuitos:
            linhas.append(f"{c.numero}. {c.descricao}: {c.potencia_va:.0f} VA, {c.tensao} V, Ib {c.corrente:.1f} A, "
                          f"disj {c.disjuntor} A, {c.secao:g} mm², queda {c.queda_pct:.2f}%, DR {'sim' if c.dr else 'não'}")
        return "\n".join(linhas)

    def nova_conversa(self) -> None:
        self.assistente.limpar()
        self.chat.config(state="normal")
        self.chat.delete("1.0", tk.END)
        self.chat.config(state="disabled")
        self._chat_add("info", "Conversa nova. Manda a dúvida! ⚡\n")

    # ===================================================== aba CONFIG
    def _aba_config(self) -> None:
        f = tk.Frame(self.aba_cfg, bg=BG)
        f.pack(padx=30, pady=20, anchor="nw")
        self._label(f, "PROVEDOR DE IA", fg=LARANJA, font=(FONTE, 12, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 10))
        self.v_prov = tk.StringVar(value=self.cfg.provedor)
        ttk.Combobox(f, textvariable=self.v_prov, values=PROVEDORES, state="readonly",
                     width=40).grid(row=0, column=1, sticky="w", padx=8)

        self.v_cfg: dict[str, tk.StringVar] = {}
        campos = [("groq_chave", "Chave Groq:", True), ("groq_modelo", "Modelo Groq ('auto' = descobre):", False),
                  ("gemini_chave", "Chave Gemini:", True), ("gemini_modelo", "Modelo Gemini ('auto' = descobre):", False),
                  ("ollama_url", "URL do Ollama:", False), ("ollama_modelo", "Modelo Ollama:", False)]
        for i, (chave, txt, secreto) in enumerate(campos, start=1):
            self._label(f, txt, font=(FONTE, 10)).grid(row=i, column=0, sticky="w", pady=5)
            var = tk.StringVar(value=getattr(self.cfg, chave))
            self.v_cfg[chave] = var
            self._entry(f, 50, textvariable=var, show="•" if secreto else "").grid(row=i, column=1, sticky="w", padx=8)

        bts = tk.Frame(f, bg=BG)
        bts.grid(row=8, column=0, columnspan=2, sticky="w", pady=16)
        self._botao(bts, "💾 SALVAR E APLICAR", self.salvar_config).pack(side="left")
        self._botao(bts, "🧪 TESTAR CONEXÃO", self.testar_ia, cor=BG3, fg=BRANCO).pack(side="left", padx=8)

        ajuda = ("COMO PEGAR UMA IA GRÁTIS:\n"
                 "• Groq → console.groq.com/keys (sem cartão, muito rápida). Recomendado.\n"
                 "• Gemini → aistudio.google.com/apikey (conta Google).\n"
                 "• Ollama → ollama.com, instale e rode 'ollama pull llama3.2' (offline, roda no seu PC).\n"
                 "• Sem nada? O modo Offline responde o básico da NBR 5410.\n\n"
                 "A chave fica em me_helper_config.json, na mesma pasta do app (fora do código!).\n"
                 "Também dá pra usar as "
                 "variáveis de ambiente GROQ_API_KEY e GEMINI_API_KEY.")
        self._label(f, ajuda, fg=CINZA, font=(FONTE, 9), justify="left").grid(row=9, column=0, columnspan=2, sticky="w")
        for i, (txt, url) in enumerate([("Abrir Groq", "https://console.groq.com/keys"),
                                        ("Abrir Gemini", "https://aistudio.google.com/apikey"),
                                        ("Abrir Ollama", "https://ollama.com/download")]):
            self._botao(bts, txt, lambda u=url: webbrowser.open(u), cor=BG2, fg=AZUL).pack(side="left", padx=4)

    def salvar_config(self) -> None:
        self.cfg.provedor = self.v_prov.get()
        for k, v in self.v_cfg.items():
            setattr(self.cfg, k, v.get().strip())
        try:
            self.cfg.salvar()
        except OSError as e:
            messagebox.showwarning("Config", f"Não salvei no disco ({e}), mas apliquei nesta sessão.")
        self._aplicar_provedor()

    def _aplicar_provedor(self, silencioso: bool = False) -> None:
        try:
            prov = criar_provedor(self.cfg)
        except IAErro as e:
            prov = OfflineProvedor()
            if not silencioso:
                messagebox.showwarning("IA", f"{e}\n\nUsando modo Offline por enquanto.")
        self.assistente.provedor = prov
        self.lbl_prov.config(text=f"Conectado a: {prov}")
        self._set_status(f"IA ativa: {prov}")

    def testar_ia(self) -> None:
        self.salvar_config()
        self.enviar("Responda só: 'Conexão OK ⚡' e o nome do modelo que você é.")

    # ================================================== atualização geral
    def atualizar_tudo(self) -> None:
        p = self.projeto
        # --- tabela de ambientes
        self.tree_amb.delete(*self.tree_amb.get_children())
        for a in p.ambientes:
            self.tree_amb.insert("", tk.END, iid=a.id, text=a.nome, tags=("aviso",) if a.avisos else (),
                                 values=(a.TIPO, f"{a.area:g}", f"{a.perimetro:g}", a.iluminacao_va,
                                         f"{a.qtd_tug} ({a.tug_va} VA)", f"{a.tue_va:.0f}", f"{a.total_va:.0f}",
                                         "✔" if a.PRECISA_DR else ""))
        # --- quadro
        self.tree_qd.delete(*self.tree_qd.get_children())
        try:
            self.circuitos = montar_quadro(p)
            erro_qd = ""
        except MEHelperErro as e:
            self.circuitos, erro_qd = [], f"⚠ {e}"
        for c in self.circuitos:
            tags = ("queda",) if c.queda_pct > 3 else ("tue",) if c.tipo == TIPO_TUE else ()
            self.tree_qd.insert("", tk.END, tags=tags, values=(
                f"{c.numero:02d} · {c.descricao}", f"{c.potencia_va:.0f}", c.tensao, f"{c.corrente:.2f}",
                c.disjuntor, f"{c.secao:g}", f"{c.iz_corrigida:.1f}", f"{c.queda_pct:.2f}",
                "✔" if c.dr else "", c.criterio))
        r = resumo_quadro(self.circuitos)
        fca = fator_agrupamento(p.circuitos_agrupados)
        fct = fator_temperatura(p.temperatura_ambiente)
        self.lbl_qd.config(text=erro_qd or (
            f"{r['circuitos']} circuitos + {r['reservas']} espaços reserva  |  {r['com_dr']} com DR 30 mA  |  "
            f"maior queda {r['maior_queda']:.2f}%  |  FCA {fca} × FCT {fct}"), fg=VERMELHO if erro_qd else BRANCO)
        # --- resumo
        self.lbl_resumo.config(text=(
            f"Ambientes: {len(p.ambientes)}\n"
            f"Iluminação: {p.total_iluminacao_va} VA   |   TUGs: {p.total_tug_va} VA   |   TUEs: {p.total_tue_va:.0f} VA\n"
            f"POTÊNCIA INSTALADA: {p.total_va:,.0f} VA   ≈ {p.total_w:,.0f} W  (FP 1,0 ilum. / 0,8 TUG)\n"
            f"Circuitos no quadro: {r['circuitos']} (+{r['reservas']} reservas)").replace(",", "."))
        titulo = f"ME Helper v{__version__} — {p.nome}{' •' if self.alterado else ''}"
        self.root.title(titulo)
        self._preview()

    def _marcar_alterado(self) -> None:
        self.alterado = True

    # ======================================================== arquivos
    def _confirmar_descartar(self) -> bool:
        if not self.alterado:
            return True
        r = messagebox.askyesnocancel("Alterações não salvas", "Salvar o projeto antes?")
        if r is None:
            return False
        return self.salvar() if r else True

    def _carregar_projeto_na_tela(self) -> None:
        self.v_proj_nome.set(self.projeto.nome)
        self.v_tensao.set(str(self.projeto.tensao))
        self.v_agrup.set(str(self.projeto.circuitos_agrupados))
        self.v_temp.set(str(self.projeto.temperatura_ambiente))
        self.limpar_formulario()
        self.atualizar_tudo()

    def novo(self) -> None:
        if self._confirmar_descartar():
            self.projeto, self.arquivo, self.alterado = Projeto(), None, False
            self._carregar_projeto_na_tela()

    def abrir(self) -> None:
        if not self._confirmar_descartar():
            return
        caminho = filedialog.askopenfilename(filetypes=[("Projeto ME Helper", "*.mehelper.json *.json")])
        if not caminho:
            return
        try:
            self.projeto = abrir_projeto(caminho)
        except MEHelperErro as e:
            messagebox.showerror("Abrir", str(e))
            return
        self.arquivo, self.alterado = Path(caminho), False
        self._carregar_projeto_na_tela()
        self._set_status(f"Aberto: {caminho}")

    def salvar(self, como: bool = False) -> bool:
        self._renomear_projeto()
        if como or not self.arquivo:
            caminho = filedialog.asksaveasfilename(defaultextension=".mehelper.json",
                                                   initialfile=f"{self.projeto.nome}.mehelper.json",
                                                   filetypes=[("Projeto ME Helper", "*.mehelper.json")])
            if not caminho:
                return False
            self.arquivo = Path(caminho)
        try:
            salvar_projeto(self.projeto, self.arquivo)
        except MEHelperErro as e:
            messagebox.showerror("Salvar", str(e))
            return False
        self.alterado = False
        self.atualizar_tudo()
        self._set_status(f"💾 Salvo em {self.arquivo}")
        return True

    def exportar_csv(self) -> None:
        if not self.projeto.ambientes:
            messagebox.showwarning("Exportar", "O projeto está vazio.")
            return
        caminho = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=f"{self.projeto.nome}.csv",
                                               filetypes=[("CSV (Excel)", "*.csv")])
        if caminho:
            try:
                exportar_csv(self.projeto, caminho, self.circuitos)
                self._set_status(f"📊 CSV exportado: {caminho}")
            except MEHelperErro as e:
                messagebox.showerror("Exportar", str(e))

    def exportar_memorial(self) -> None:
        if not self.projeto.ambientes:
            messagebox.showwarning("Memorial", "O projeto está vazio.")
            return
        caminho = filedialog.asksaveasfilename(defaultextension=".html", initialfile=f"Memorial - {self.projeto.nome}.html",
                                               filetypes=[("Página HTML", "*.html")])
        if caminho:
            try:
                exportar_memorial(self.projeto, caminho)
            except MEHelperErro as e:
                messagebox.showerror("Memorial", str(e))
                return
            webbrowser.open(Path(caminho).resolve().as_uri())
            self._set_status("📄 Memorial aberto no navegador (Ctrl+P → Salvar como PDF).")

    def sair(self) -> None:
        if self._confirmar_descartar():
            self.root.destroy()


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


##############################################################################
#  PONTO DE ENTRADA
##############################################################################
if __name__ == "__main__":
    main()
