#!python3
# -*- coding: cp1252 -*-

import os
import re
import shutil
import tempfile
import warnings
import locale
from openpyxl import load_workbook

from sisposbase.get_sql_data import getsqldata
from sisposbase.sispos import BaseSISPOS

warnings.filterwarnings("ignore")

sqlcode = """
SELECT 
	e.matricula, e.nome, 
	f.codigo codfunc, f.nome profissao, 
	d.sigla depto, 
	t.codigo tipo_MO, 
	s.codigo Situa
from 
	Empregado e, 
	Departamento d, 
	Funcao f, 
	TipoMaoObra t, 
	Situacao s
where 
	e.fkDepartamento = d.pkDepartamento and
	e.fkFuncao = f.pkFuncao and
	e.fkTipoMaoObra = t.pkTipoMaoObra and
	e.fkSituacao = s.pkSituacao
"""

sqlrunner = os.path.join(os.getcwd(), "SingleSQLExecutor.exe")


def levenshtein(s, t):
    """ From Wikipedia article; Iterative with two matrix rows. """
    if s == t:
        return 0
    elif len(s) == 0:
        return len(t)
    elif len(t) == 0:
        return len(s)
    v0 = [None] * (len(t) + 1)
    v1 = [None] * (len(t) + 1)
    for i in range(len(v0)):
        v0[i] = i
    for i in range(len(s)):
        v1[0] = i + 1
        for j in range(len(t)):
            cost = 0 if s[i] == t[j] else 1
            v1[j + 1] = min(v1[j] + 1, v0[j + 1] + 1, v0[j] + cost)
        for j in range(len(v0)):
            v0[j] = v1[j]

    return v1[len(t)]


class ComparaMNFDT(object):
    TIPO_MO_DIRETA = "0"
    TIPO_MO_INDIRETA = "1"
    TIPO_MO_NAOAPROP = "2"

    SITUACAO_NCPDEMITIDO = "10"

    def __init__(self, rpessi, rpessiname, empregados, empregadosname):

        # LEFT : DESTINO (resultado final)
        self.rpessi_data = rpessi
        self.rpessi_name = rpessiname

        # RIGHT: Origem (dados iniciais)
        self.empreg_data = empregados
        self.empreg_name = empregadosname

    def compara(self):

        empregdict = self.empreg_data

        mudsql = []
        rsql = lambda x: mudsql.append(str(x))

        mudtxt = []
        rtxt = lambda x: mudtxt.append(str(x))

        bannermsg = '000 Transformando a partir de "%s" para "%s".' % (
            self.empreg_name,
            self.rpessi_name,
        )
        rtxt(bannermsg)

        qtddif = 0
        for linha in self.rpessi_data:
            matr, nome, codfunc, descricao, depto, tipo = linha

            muds = []

            if matr in empregdict:

                # codfunc
                if (codfunc != empregdict[matr]["codfunc"]) and (
                    descricao.find("(I)") == -1
                ):
                    muds.append(
                        "  CODFUNC | de %s(%s) para %s(%s)"
                        % (
                            empregdict[matr]["codfunc"],
                            empregdict[matr]["descricao"],
                            codfunc,
                            descricao,
                        )
                    )
                    codfuncsql = (
                        'update empregados set codfunc = "%s" where matr = %s;'
                        % (codfunc, matr)
                    )
                    rsql(codfuncsql)

                # departamento
                if depto != empregdict[matr]["depto"]:
                    if depto == "IT" and empregdict[matr]["depto"] == "IT-APRENDIZES":
                        pass
                    else:
                        muds.append(
                            '  DEPTO | de "%s" para "%s"'
                            % (empregdict[matr]["depto"], depto)
                        )
                        deptosql = (
                            'update empregados set depto = "%s" where matr = %s;'
                            % (depto, matr)
                        )
                        rsql(deptosql)

                # tipo
                if tipo != empregdict[matr]["tipo"]:
                    if depto == "IT" and empregdict[matr]["depto"] == "IT-APRENDIZES":
                        pass
                    else:
                        muds.append(
                            '  TIPO | de "%s" para "%s" [avaliar: %s %s/%s]'
                            % (
                                empregdict[matr]["tipo"],
                                tipo,
                                empregdict[matr]["depto"],
                                descricao,
                                empregdict[matr]["descricao"],
                            )
                        )

                # situacao = demitido
                if empregdict[matr]["situacao"] in (self.SITUACAO_NCPDEMITIDO,):
                    muds.append(
                        u"""  ---------
    Aten��o: matr %s consta como DEMITIDO na base -%s-:
    matr: %s / nome: %s
    codfunc: %s (%s)
    depto: %s / tipo: %s
  ---------"""
                        % (
                            matr,
                            self.empreg_name,
                            matr,
                            nome,
                            codfunc,
                            descricao,
                            depto,
                            tipo,
                        )
                    )

            else:
                if tipo != self.TIPO_MO_NAOAPROP:
                    muds.append(
                        u"""  ---------
    Aten��o: matr %s nao existe na base -%s-:
    matr: %s / nome: %s
    codfunc: %s (%s)
    depto: %s / tipo: %s
  ---------"""
                        % (
                            matr,
                            self.empreg_name,
                            matr,
                            nome,
                            codfunc,
                            descricao,
                            depto,
                            tipo,
                        )
                    )

            if muds:
                rtxt("\nMATR: %s (%s):" % (str(matr).ljust(5), nome.ljust(20)[0:17]))

                qtddif += len(muds)

                for mud in muds:
                    rtxt(mud)

        return [mudtxt, mudsql, qtddif]


class ComparaRpessiEmpregados(BaseSISPOS):
    """Compara a planilha [Rela��o de Pessoal do I] com a listagem de Empregados do ControleProducao"""

    findfiles = [("!RPESSI", r"RELA��O.*EFETIVO.*\.xlsx")]

    def getrpessidata(self, rpessifname):

        # Abrir aba geral da planilha usando openPYXL
        # Ignorar 'warning' usando fun��o espec�fica
        with warnings.catch_warnings():
            wb = load_workbook(rpessifname)
            abageral = wb.get_sheet_by_name("GERAL")

        rows = [[x.value for x in row] for row in abageral.rows]

        # achar intervalo que contem os nomes de pessoas.
        namestart = -1
        nameend = -1
        for row, rowno in zip(rows, range(len(rows))):
            nome, setor = row[1:3]

            if namestart == -1:
                if nome == "Nome" and setor == "Setor":
                    # Se a linha atual contem "Nome" e " Setor" o inicio dos nomes � na pr�xima linha.
                    namestart = rowno + 1
                    # print u"Encontrado in�cio dos nomes na linha %d" % (namestart+1,)
            if nameend == -1:
                if nome == None and setor == None:
                    # Se a linha atual contem None o fim dos nomes � na pen�ltima linha.
                    nameend = rowno - 1
                    # print u"Encontrado fim dos nomes na linha %d" % (nameend+1,)

        employeelist = rows[namestart : nameend + 1]

        retval = []
        for employee in employeelist:
            # [3567, u'LIBERAL ENIO ZANELATTO', u'I', 29, '=CONCATENATE(C2," ",G2)', None, u'DIRETOR', None, 2, 1, None, None, None, None, None, None]
            matr = str(employee[0])
            nome = employee[1]
            depto = str(employee[2])
            codfunc = str(employee[3])
            prof = employee[6]
            tipo = str(employee[8])

            # add data
            entry = [matr, nome, codfunc, prof, depto, tipo]

            retval.append(entry)

        return retval

    def getempregdata(self):

        empregados_data = getsqldata(sqlcode)[0]

        retval = {}
        for line in empregados_data[1:]:
            line_s = [x.strip() for x in line]

            # matr, nome, codfunc, descricao, depto, tipo = linha
            matr, nome, codfunc, descricao, depto, tipo, situacao = line_s

            retval[matr] = {}
            retval[matr]["nome"] = nome
            retval[matr]["codfunc"] = codfunc
            retval[matr]["descricao"] = descricao
            retval[matr]["depto"] = depto
            retval[matr]["tipo"] = tipo
            retval[matr]["situacao"] = situacao

        return retval

    def process(self, f):

        # Relacao de Pessoal do I (matriz)
        rpessidata = self.getrpessidata(f["!RPESSI"])

        # Relacao de Empregados no Sistema (dicionario)
        empregdata = self.getempregdata()

        # print "matr, nome, codfunc, descricao, depto, situacao = linha"
        # from code import interact; interact(local=locals())

        # Ativa o comparador
        comp = ComparaMNFDT(rpessidata, "RPESSI", empregdata, "TABELA EMPREGADOS")

        # Realiza compara��o, grava resultados em mudtxt e mudsql, qtdmud = quantidade de mudan�as
        mudtxt, mudsql, qtdmud = comp.compara()

        print("----------------------------------------")
        print("%d diferen�a(s) detectadas..." % (qtdmud))
        print("----------------------------------------")
        print("")

        if qtdmud:
            # Salvando em texto as mudan�as textuais
            txtfile = self.getoutputfile(append="txt")
            txtdata = u"\n".join(mudtxt)
            txtfile.write(txtdata)


if __name__ == "__main__":
    a = ComparaRpessiEmpregados()
    a.run()
