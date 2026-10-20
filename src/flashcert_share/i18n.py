"""The few strings the server renders itself; the course page's strings live in static/i18n.js."""

STRINGS = {
    "en": {
        "courses": "Shared courses",
        "intro": "Notes, revision sheets, quizzes and flashcards. Open a course to study it: no account needed.",
        "search": "Search a course",
        "no_courses": "No course has been shared yet.",
        "no_match": "No course matches your search.",
        "other": "Other courses",
        "questions": "questions",
        "cards": "flashcards",
        "updated": "Updated",
        "not_found": "This course doesn't exist, or it is no longer shared.",
        "back": "All courses",
        "progress": "Your progress",
        "palette": "Color palette",
        "appearance": "Appearance",
        "theme_system": "System",
        "theme_light": "Light",
        "theme_dark": "Dark",
        "noscript": "This page needs JavaScript to show the course.",
        "add_to_app": "Add to FlashCert",
        "add_to_app_help": "Opens the FlashCert app on this device. It adds the course and this server by itself.",
        "course_count": "courses",
    },
    "fr": {
        "courses": "Cours partagés",
        "intro": "Notes, fiches de révision, quiz et cartes mémoire. Ouvrez un cours pour réviser : aucun compte nécessaire.",
        "search": "Rechercher un cours",
        "no_courses": "Aucun cours n'a encore été partagé.",
        "no_match": "Aucun cours ne correspond à votre recherche.",
        "other": "Autres cours",
        "questions": "questions",
        "cards": "cartes",
        "updated": "Mis à jour le",
        "not_found": "Ce cours n'existe pas, ou il n'est plus partagé.",
        "back": "Tous les cours",
        "progress": "Votre progression",
        "palette": "Palette de couleurs",
        "appearance": "Apparence",
        "theme_system": "Système",
        "theme_light": "Clair",
        "theme_dark": "Sombre",
        "noscript": "Cette page a besoin de JavaScript pour afficher le cours.",
        "add_to_app": "Ajouter à FlashCert",
        "add_to_app_help": "Ouvre l'application FlashCert sur cet appareil. Elle ajoute le cours et ce serveur d'elle-même.",
        "course_count": "cours",
    },
}


def strings(lang: str) -> dict:
    return STRINGS.get(lang, STRINGS["en"])
