"""Tests R43-W (2026-05-19) — `truststore.inject_into_ssl()` au démarrage
du pipeline pour basculer Python sur le truststore SSL système.

Constat 19/05 : sources Sénat à 0 items depuis 18/05 17:39 UTC sur 3 runs
consécutifs (alertes FORMAT_DRIFT fired). Cause : Sénat a rotaté son
cert SSL vers une chaîne `Gandi SAS, CN=GandiCert` pas dans le bundle
`certifi` embarqué côté runners GHA.

Erreur observée dans les logs daily.yml :
    httpcore.ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate
    verify failed: unable to get local issuer certificate (_ssl.c:1016)

Fix : `truststore.inject_into_ssl()` bascule SSLContext.create_default()
sur les CA du système (Ubuntu /etc/ssl/certs/ca-certificates.crt, macOS
Keychain). Le CA bundle système se met à jour automatiquement via le
paquet OS `ca-certificates`, donc on n'aura plus à courir derrière les
rotations de certs gouvernementaux.

Tests :
1. Le module `truststore` est bien dépendance déclarée (pyproject.toml)
2. L'import de `src.main` appelle bien `truststore.inject_into_ssl()`
3. Fallback gracieux si `truststore` n'est pas installé (no-op)
4. (Smoke test optionnel) httpx vers senat.fr ne plante pas SSL
   — skip si pas de réseau pour ne pas faire échouer la CI offline
"""
from __future__ import annotations

import ssl
import sys

import pytest


def test_r43w_truststore_est_dans_dependencies():
    """Garde-fou contrat : `truststore` doit être déclaré dans pyproject.toml.
    Sinon le runner GHA Free installerait Python sans truststore → fallback
    silencieux → bug SSL revient."""
    from pathlib import Path

    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    content = pyproject.read_text()
    assert 'truststore' in content, (
        "`truststore` doit figurer dans pyproject.toml [project.dependencies]"
    )


def test_r43w_import_main_active_truststore(monkeypatch):
    """À l'import de `src.main`, `truststore.inject_into_ssl()` doit être
    appelé (si truststore est dispo). Vérifie via le SSLContext.create_default
    qui change de classe après l'inject."""
    # Si src.main est déjà importé (autres tests), il faut le reload pour
    # capturer l'effet de l'inject. Mais inject_into_ssl est idempotent —
    # on vérifie juste l'état du SSLContext courant.
    # truststore remplace ssl.SSLContext.__init__ pour TLS_CLIENT — on peut
    # vérifier que c'est bien la version truststore.
    import src.main  # noqa: F401 (déclenche l'inject à l'import)
    # truststore.inject_into_ssl() patche ssl._create_default_https_context
    # et ssl.SSLContext. La vérif la plus simple : importer truststore
    # et vérifier qu'il considère le contexte par défaut comme « injecté ».
    try:
        import truststore
    except ImportError:
        pytest.skip("truststore non installé — test no-op")
    # truststore.SSLContext est une sous-classe ssl.SSLContext. Après
    # inject_into_ssl(), `ssl.create_default_context()` renvoie un contexte
    # de type truststore.SSLContext.
    ctx = ssl.create_default_context()
    assert isinstance(ctx, truststore.SSLContext), (
        "ssl.create_default_context() doit retourner un truststore.SSLContext "
        "après inject_into_ssl(). État SSL Python pas correctement basculé."
    )


def test_r43w_fallback_si_truststore_absent(monkeypatch):
    """Si `truststore` n'est pas installé (env minimal / CI sans deps),
    l'import de `src.main` doit fonctionner quand même (fallback silencieux).
    Vérifie via patch du sys.modules → import → l'ImportError est absorbé.
    """
    # Force ImportError sur `import truststore` en virant la lib du sys.modules
    # et en cachant le module.
    monkeypatch.setitem(sys.modules, "truststore", None)
    # Re-importer src.main sans truststore : ne doit pas crasher
    if "src.main" in sys.modules:
        del sys.modules["src.main"]
    import importlib
    try:
        # On ne peut pas vraiment "désinstaller" truststore depuis le
        # processus en cours, mais on peut tester que le bloc try/except
        # ImportError dans src/main.py est bien en place. Le code est :
        #     try:
        #         import truststore
        #         truststore.inject_into_ssl()
        #     except ImportError:
        #         pass
        # → si truststore est dispo dans cet env, le test ne « prouve »
        # pas que le fallback marche, mais vérifie au moins que l'import
        # ne crashe pas dans un env normal.
        importlib.import_module("src.main")
    except ImportError as e:
        # Si truststore est marqué None dans sys.modules, l'import bascule
        # sur l'ImportError → le try/except du main doit l'absorber.
        # Si on arrive ici, c'est que le fallback n'est pas en place.
        pytest.fail(
            f"L'import de src.main a crashé sur truststore absent : {e} "
            "→ le try/except ImportError de R43-W n'est pas en place."
        )


@pytest.mark.skipif(
    "CI" in __import__("os").environ,
    reason="Smoke test réseau senat.fr — skip en CI pour rester offline-safe",
)
def test_r43w_smoke_httpx_senat_via_truststore():
    """Smoke test live : avec truststore inject, httpx doit réussir à
    valider le cert de www.senat.fr. Skip en CI (pas de réseau garanti)."""
    import src.main  # noqa: F401
    import httpx

    with httpx.Client(timeout=8) as c:
        r = c.get("https://www.senat.fr/akomantoso/depots.xml")
        assert r.status_code == 200
        assert len(r.content) > 1000, "Réponse anormalement courte"
