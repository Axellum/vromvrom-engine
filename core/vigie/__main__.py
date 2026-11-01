"""
python -m core.vigie — essais de la vigie à la main.

  --une-passe --a-blanc      passage réel EN LECTURE : lit les sources, trie,
                             affiche ce qui SERAIT envoyé ; n'envoie rien et
                             n'écrit pas l'état.
  --a-blanc --essai-tri N    en plus, trie les N éléments les plus récents de
                             chaque source comme s'ils étaient nouveaux.
  --une-passe                un vrai passage (envoie et écrit l'état).
  --test-notif               une seule notification de test au téléphone.

La console n'affiche jamais le contenu d'un mail : source, niveau, expéditeur
tronqué, longueur de l'objet et du résumé seulement.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys

from core.vigie.modeles import LIBELLE_CONSOLE, Niveau


def _expediteur_court(element) -> str:
    from core.vigie.sources.gmail import nom_expediteur

    nom = nom_expediteur(element.expediteur)
    return nom if len(nom) <= 14 else nom[:13] + "…"


def afficher(rapport, reglages) -> None:
    from core.vigie.boucle import libelle_source
    from core.vigie.tri_gmail import categorie

    if rapport.saute:
        print("Passage sauté : un autre passage tient le verrou.")
        return
    if rapport.a_blanc:
        print("Vigie — passage à blanc : rien n'est envoyé, l'état n'est pas écrit.")
    for src in rapport.sources:
        statut = "OK" if src.ok else f"ÉCHEC ({src.echecs_consecutifs} de suite) : {'; '.join(src.erreurs)}"
        ligne = f"[{src.nom}] {statut} — {src.detail}"
        if src.premier_passage:
            ligne += f" — premier passage : {src.nb_marques_vus} marqué(s) vu(s), aucune alerte"
        print(ligne)

    if rapport.elements:
        print("Éléments nouveaux (ou essai de tri) :")
    for e in rapport.elements:
        niveau = LIBELLE_CONSOLE[e.niveau]
        if e.a_trier:
            desabo = "désabo" if e.desabonnement else "sans désabo"
            print(f"  {e.source} | {niveau} | {_expediteur_court(e)} | objet {len(e.objet)} car. "
                  f"| {categorie(e.labels)}, {desabo} | {e.methode_tri} | message {len(e.message)} car.")
        elif e.source == "vigie":
            print(f"  {e.source} | {niveau} | {e.titre}")
        else:  # forum, GitHub, YouTube : public
            print(f"  {e.source} | {niveau} | {e.titre} | {e.lien}")

    print("Comptes par source et niveau :")
    sources = list(dict.fromkeys([s.nom for s in rapport.sources] + [e.source for e in rapport.elements]))
    for source in sources:
        des = [e for e in rapport.elements if e.source == source]
        comptes = ", ".join(f"{LIBELLE_CONSOLE[n].lower()} {sum(1 for e in des if e.niveau == n)}" for n in Niveau)
        print(f"  {libelle_source(source)} : {comptes}")

    if rapport.tri is not None:
        t = rapport.tri
        autres = (f"{t.regles} par règles ; {t.categorie} par catégorie Gmail ; "
                  f"{t.preferences} par préférence d'expéditeur.")
        if t.llm_refuses:
            print(f"Modèles REFUSÉS pour les mails (inconnus ou non locaux, jamais appelés) : {t.llm_refuses}")
        if t.llm_modeles:
            moyenne = (sum(t.llm_durees_s) / len(t.llm_durees_s)) if t.llm_durees_s else 0.0
            print(f"Tri des mails : cascade locale {t.llm_modeles}, {t.llm_appels} appel(s), "
                  f"{t.llm_ok} au format, {moyenne:.1f} s en moyenne "
                  f"(max {max(t.llm_durees_s, default=0):.1f} s) ; {autres}")
        else:
            print(f"Tri des mails : sans LLM ({t.raison_sans_llm or 'rien à demander'}) ; {autres}")

    verbe = "qui seraient envoyées" if rapport.a_blanc else "envoyées"
    print(f"Notifications {verbe} : {len(rapport.envois)}")
    for envoi in rapport.envois:
        if envoi["canal"] == "telephone":
            print(f"  téléphone [{envoi['data'].get('tag')}] titre {len(envoi['title'])} car., "
                  f"message {len(envoi['message'])} car., lien {'oui' if envoi['data'].get('clickAction') else 'non'}")
        else:
            print(f"  persistante HA [{envoi['notification_id']}] message {len(envoi['message'])} car.")
    silence = f"{reglages.silence[0]}-{reglages.silence[1]}" if reglages.silence else "aucun"
    print(f"Silence de nuit ({silence}) : {'en cours' if rapport.silence else 'non'} ; "
          f"alertes en file : {rapport.en_attente}")
    print(f"Durée : {rapport.duree_s:.1f} s")


async def _test_notif(reglages) -> int:
    from core.vigie.notification import Notifieur

    if not reglages.notify_service:
        print("MOTEUR_VIGIE_NOTIFY_SERVICE absent ou invalide : rien n'est envoyé.")
        return 1
    ok = await Notifieur(reglages.notify_service).telephone(
        "Vigie : notification de test",
        "Si tu lis ceci, la vigie du moteur sait joindre ton téléphone.",
        tag="vigie-test", urgent=True,
    )
    print(f"notify.{reglages.notify_service} : {'accepté par Home Assistant' if ok else 'ÉCHEC (voir le journal)'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m core.vigie", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--une-passe", action="store_true")
    parser.add_argument("--a-blanc", action="store_true")
    parser.add_argument("--essai-tri", type=int, default=0, metavar="N")
    parser.add_argument("--test-notif", action="store_true")
    parser.add_argument("-v", "--bavard", action="store_true", help="journal INFO sur stderr")
    args = parser.parse_args(argv)

    # Console Windows en cp1252 : « … » et « → » doivent s'afficher sans planter.
    with contextlib.suppress(AttributeError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO if args.bavard else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    from core.env_bootstrap import bootstrap_env

    bootstrap_env()
    from core.vigie.boucle import Vigie
    from core.vigie.reglages import lire_reglages

    reglages = lire_reglages()
    if args.test_notif:
        return asyncio.run(_test_notif(reglages))
    if not args.une_passe:
        parser.error("indiquer --une-passe (avec --a-blanc pour un essai sans effet) ou --test-notif")
    if args.essai_tri and not args.a_blanc:
        parser.error("--essai-tri n'existe qu'avec --a-blanc")
    if not args.a_blanc and not reglages.notify_service:
        print("MOTEUR_VIGIE_NOTIFY_SERVICE absent ou invalide : un vrai passage ne pourrait rien envoyer.")
        return 1
    vigie = Vigie(reglages, a_blanc=args.a_blanc)
    if not vigie.collecteurs:
        print("Aucune source activée (MOTEUR_VIGIE_GMAIL, MOTEUR_VIGIE_FORUMS, MOTEUR_VIGIE_GITHUB_PSEUDO, "
              "MOTEUR_VIGIE_YOUTUBE_CHAINE).")
        return 1
    rapport = asyncio.run(vigie.passe(essai_tri=max(args.essai_tri, 0)))
    afficher(rapport, reglages)
    return 0


if __name__ == "__main__":
    sys.exit(main())
