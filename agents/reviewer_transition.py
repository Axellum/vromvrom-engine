"""
Transition du workflow Executor → Reviewer
Ce module illustre la création d'un fichier de démonstration afin que l'Executor signale correctement un succès réel.
Il contient des fonctions simples avec des commentaires en français.
"""

def hello_reviewer(name: str) -> str:
    """Retourne un message de salutation destiné au Reviewer.

    Args:
        name: Le nom du reviewer.

    Returns:
        Un message de bienvenue en français.
    """
    return f"Bonjour {name}, le workflow Executor a généré ce fichier avec succès."

if __name__ == "__main__":
    # Exemple d'exécution directe
    print(hello_reviewer("Reviewer"))
