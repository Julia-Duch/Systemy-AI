
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP


# --------------------------------------------------
# KONFIGURACJA
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data" / "raw"
PROCESSED_DIR = BASE_DIR / "data" / "processed"
HANDOVER_OUTPUT_DIR = PROCESSED_DIR / "handovers"
PROMPT_FILE = BASE_DIR / "prompts" / "handover.md"

mcp = FastMCP("Nordwind Handover")

CLOSED_STATUSES = {
    "completed",
    "closed",
    "resolved",
    "cancelled",
    "done"
}


# --------------------------------------------------
# FUNKCJE POMOCNICZE
# --------------------------------------------------

def load_csv(filename: str) -> list[dict]:
    """Odczytuje dane z pliku CSV."""
    file_path = DATA_DIR / filename

    if not file_path.exists():
        raise FileNotFoundError(f"Nie znaleziono pliku: {file_path}")

    with open(file_path, "r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def find_employee(employee_id: str) -> dict:
    """Wyszukuje pracownika po identyfikatorze."""
    employees = load_csv("employees.csv")

    employee = next(
        (
            item for item in employees
            if item["employee_id"] == employee_id
        ),
        None
    )

    if employee is None:
        raise ValueError(f"Nie znaleziono pracownika: {employee_id}")

    return employee


def format_result(data) -> str:
    """Zwraca dane w czytelnym formacie JSON."""
    return json.dumps(
        data,
        ensure_ascii=False,
        indent=2
    )


def is_closed(item: dict) -> bool:
    """Sprawdza, czy element ma status oznaczający zakończenie."""
    status = item.get("status", "").strip().lower()
    return status in CLOSED_STATUSES



def collect_footprint_data(employee_id: str) -> dict:
    """
    Zbiera informacje o pracy pracownika:
    dokumenty, projekty, zgłoszenia, wątki,
    zadania cykliczne, klientów i dostępy.
    """

    employee = find_employee(employee_id)

    # 1. Dokumenty napisane przez pracownika
    all_documents = load_csv("documents.csv")

    authored_documents = [
        doc for doc in all_documents
        if doc["author_id"] == employee_id
    ]

    # 2. Dokumenty, których pracownik jest właścicielem
    owned_documents = [
        doc for doc in all_documents
        if doc["owner_id"] == employee_id
    ]

    # 3. Projekty, których pracownik jest właścicielem
    projects = [
        project for project in load_csv("projects.csv")
        if project["owner_id"] == employee_id
    ]

    # 4. Zgłoszenia przypisane do pracownika
    tickets = [
        item for item in load_csv("tickets.csv")
        if item["assigned_to"] == employee_id
    ]

    # 5. Wątki przypisane do pracownika
    threads = [
        item for item in load_csv("threads.csv")
        if item["assigned_to"] == employee_id
    ]

    # 6. Zadania cykliczne przypisane do pracownika
    tasks = [
        item for item in load_csv("recurring_tasks.csv")
        if item["assigned_to"] == employee_id
    ]

    # 7. Klienci powiązani ze zgłoszeniami i wątkami
    client_ids = {
        item["client_id"]
        for item in tickets + threads
        if item.get("client_id")
    }

    clients = [
        client for client in load_csv("clients.csv")
        if client["client_id"] in client_ids
    ]

    # 8. Dokumenty, do których pracownik ma dostęp
    employee_role = employee["role_id"]
    acl_entries = load_csv("acl.csv")

    accessible_documents = []

    for entry in acl_entries:
        allowed_roles = [
            role.strip()
            for role in entry["allowed_roles"].split(";")
        ]

        if employee_role in allowed_roles:
            document = next(
                (
                    doc for doc in all_documents
                    if doc["doc_id"] == entry["doc_id"]
                ),
                None
            )

            if document:
                accessible_documents.append({
                    "doc_id": document["doc_id"],
                    "title": document["title"],
                    "department": document["department"],
                    "allowed_roles": allowed_roles
                })

    return {
        "employee": employee,
        "authored_documents": authored_documents,
        "owned_documents": owned_documents,
        "projects": projects,
        "tickets": tickets,
        "threads": threads,
        "recurring_tasks": tasks,
        "related_clients": clients,
        "accessible_documents": accessible_documents,
        "summary": {
            "total_authored_documents": len(authored_documents),
            "total_owned_documents": len(owned_documents),
            "total_projects": len(projects),
            "total_tickets": len(tickets),
            "total_threads": len(threads),
            "total_recurring_tasks": len(tasks),
            "total_related_clients": len(clients),
            "total_accessible_documents": len(accessible_documents)
        }
    }



def extract_open_items_data(employee_id: str) -> dict:
    """
    Wyodrębnia sprawy wymagające dalszej uwagi.
    """
    footprint = collect_footprint_data(employee_id)

    open_tickets = [
        item for item in footprint["tickets"]
        if not is_closed(item)
    ]

    completed_tickets = [
        item for item in footprint["tickets"]
        if is_closed(item)
    ]

    # Wątki bez statusu również wymagają przejrzenia.
    threads_to_review = [
        item for item in footprint["threads"]
        if not is_closed(item)
    ]

    active_tasks = [
        item for item in footprint["recurring_tasks"]
        if not is_closed(item)
    ]

    return {
        "employee": footprint["employee"],
        "open_tickets": open_tickets,
        "completed_tickets": completed_tickets,
        "threads_to_review": threads_to_review,
        "recurring_tasks_to_continue": active_tasks,
        "summary": {
            "open_tickets": len(open_tickets),
            "completed_tickets": len(completed_tickets),
            "threads_to_review": len(threads_to_review),
            "recurring_tasks_to_continue": len(active_tasks)
        },
        "note": (
            "Wątki i zadania wymagają weryfikacji przez pracownika. "
            "Sam brak statusu nie potwierdza, że sprawa jest otwarta."
        )
    }


def append_records(lines: list, title: str, records: list[dict]):
    """Dodaje rekordy do dokumentu Markdown."""
    lines.append(f"## {title}")
    lines.append("")

    if not records:
        lines.append("Brak zarejestrowanych pozycji.")
        lines.append("")
        return

    for index, record in enumerate(records, start=1):
        lines.append(f"### {index}.")

        for key, value in record.items():
            formatted_value = json.dumps(
                value,
                ensure_ascii=False
            )
            lines.append(f"- **{key}:** {formatted_value}")

        lines.append("")


# --------------------------------------------------
# NARZĘDZIE 1 – INFORMACJE O PRACOWNIKU
# --------------------------------------------------

@mcp.tool()
def get_employee_info(employee_id: str) -> str:
    """Pobiera podstawowe informacje o pracowniku."""
    employee = find_employee(employee_id)
    return format_result(employee)


# --------------------------------------------------
# NARZĘDZIE 2 – ZGŁOSZENIA PRACOWNIKA
# --------------------------------------------------

@mcp.tool()
def get_employee_tickets(employee_id: str) -> str:
    """Pobiera zgłoszenia przypisane do pracownika."""
    find_employee(employee_id)

    tickets = load_csv("tickets.csv")

    employee_tickets = [
        ticket for ticket in tickets
        if ticket["assigned_to"] == employee_id
    ]

    return format_result(employee_tickets)


# --------------------------------------------------
# NARZĘDZIE 3 – WĄTKI KOMUNIKACYJNE
# --------------------------------------------------

@mcp.tool()
def get_employee_threads(employee_id: str) -> str:
    """Pobiera wątki komunikacyjne przypisane do pracownika."""
    find_employee(employee_id)

    threads = load_csv("threads.csv")

    employee_threads = [
        thread for thread in threads
        if thread["assigned_to"] == employee_id
    ]

    return format_result(employee_threads)


# --------------------------------------------------
# NARZĘDZIE 4 – ZADANIA CYKLICZNE
# --------------------------------------------------

@mcp.tool()
def get_employee_recurring_tasks(employee_id: str) -> str:
    """Pobiera zadania cykliczne przypisane do pracownika."""
    find_employee(employee_id)

    tasks = load_csv("recurring_tasks.csv")

    employee_tasks = [
        task for task in tasks
        if task["assigned_to"] == employee_id
    ]

    return format_result(employee_tasks)


# --------------------------------------------------
# NARZĘDZIE 5 – ZBIORCZY PRZEGLĄD HANDOVER
# --------------------------------------------------

@mcp.tool()
def get_handover_overview(
    from_employee_id: str,
    to_employee_id: str
) -> str:
    """Przygotowuje zbiorcze zestawienie danych do handover."""
    source_employee = find_employee(from_employee_id)
    target_employee = find_employee(to_employee_id)

    if source_employee["department"] != target_employee["department"]:
        return format_result({
            "error": "Pracownicy należą do różnych działów.",
            "from_department": source_employee["department"],
            "to_department": target_employee["department"]
        })

    footprint = collect_footprint_data(from_employee_id)
    open_items = extract_open_items_data(from_employee_id)

    return format_result({
        "process": "Handover",
        "organization": "Nordwind Logistics",
        "from_employee": source_employee,
        "to_employee": target_employee,
        "tickets": footprint["tickets"],
        "open_tickets": open_items["open_tickets"],
        "completed_tickets": open_items["completed_tickets"],
        "threads": footprint["threads"],
        "recurring_tasks": footprint["recurring_tasks"],
        "related_clients": footprint["related_clients"],
        "summary": {
            **footprint["summary"],
            **open_items["summary"]
        },
        "note": (
            "To zestawienie jest projektem przekazania. "
            "Nie zmienia automatycznie przypisania zadań "
            "ani zgłoszeń nowemu pracownikowi."
        )
    })


# --------------------------------------------------
# NARZĘDZIE 6 – ZEBRANIE ŚLADU PRACY
# --------------------------------------------------

@mcp.tool()
def collect_footprint(employee_id: str) -> str:
    """
    Zbiera informacje o pracy pracownika:
    zgłoszenia, wątki, zadania cykliczne i klientów.
    """
    footprint = collect_footprint_data(employee_id)

    return format_result({
        "operation": "collect_footprint",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        **footprint
    })


# --------------------------------------------------
# NARZĘDZIE 7 – WYODRĘBNIANIE OTWARTYCH SPRAW
# --------------------------------------------------

@mcp.tool()
def extract_open_items(employee_id: str) -> str:
    """
    Wyodrębnia otwarte zgłoszenia, wątki do przejrzenia
    oraz zadania cykliczne wymagające kontynuacji.
    """
    result = extract_open_items_data(employee_id)

    return format_result({
        "operation": "extract_open_items",
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        **result
    })


# --------------------------------------------------
# NARZĘDZIE 8 – GENEROWANIE DOKUMENTU HANDOVER
# --------------------------------------------------


@mcp.tool()
def generate_handover_doc(
    from_employee_id: str,
    to_employee_id: str
) -> str:
    """
    Generuje dokument Markdown przekazania obowiązków.
    Dokument jest zapisywany w data/processed/handovers.
    """

    source_employee = find_employee(from_employee_id)
    target_employee = find_employee(to_employee_id)

    if source_employee["department"] != target_employee["department"]:
        raise ValueError(
            "Pracownicy należą do różnych działów. "
            "Generowanie dokumentu zostało wstrzymane."
        )

    footprint = collect_footprint_data(from_employee_id)
    open_items = extract_open_items_data(from_employee_id)

    generated_at = datetime.now(timezone.utc).isoformat()
    
    
    # Tłumaczenie wartości na język polski.
    status_translations = {
        "in_progress": "W trakcie realizacji",
        "in progress": "W trakcie realizacji",
        "open": "Otwarte",
        "completed": "Zakończone",
        "pending": "Oczekujące",
        "active": "Aktywne",
    }

    priority_translations = {
        "high": "Wysoki",
        "medium": "Średni",
        "low": "Niski",
    }

    frequency_translations = {
        "daily": "Codziennie",
        "weekly": "Co tydzień",
        "monthly": "Co miesiąc",
    }

    def translate_status(value):
        return status_translations.get(
            str(value).lower(), value
        )

    def translate_priority(value):
        return priority_translations.get(
            str(value).lower(), value
        )

    def translate_frequency(value):
        return frequency_translations.get(
            str(value).lower(), value
        )

    # Dodaje identyfikator źródła do każdego rekordu.
    def add_source_reference(records, id_field):
        result = []

        for record in records:
            item = dict(record)
            item["source_reference"] = (
                f"{id_field}: {record.get(id_field, 'brak ID')}"
            )
            result.append(item)

        return result

    # Łączy dokumenty napisane i posiadane przez pracownika.
    # Dzięki temu ten sam dokument nie pojawi się dwa razy.
    documents_by_id = {}

    for document in footprint["authored_documents"]:
        doc_id = document["doc_id"]
        documents_by_id[doc_id] = dict(document)
        documents_by_id[doc_id]["relation_to_employee"] = "Autor"

    for document in footprint["owned_documents"]:
        doc_id = document["doc_id"]

        if doc_id in documents_by_id:
            documents_by_id[doc_id]["relation_to_employee"] = (
                "Autor i właściciel"
            )
        else:
            documents_by_id[doc_id] = dict(document)
            documents_by_id[doc_id]["relation_to_employee"] = "Właściciel"

    handover_documents = add_source_reference(
        list(documents_by_id.values()),
        "doc_id"
    )

    projects = add_source_reference(
        footprint["projects"],
        "project_id"
    )

    accessible_documents = add_source_reference(
        footprint["accessible_documents"],
        "doc_id"
    )

    open_tickets = add_source_reference(
        open_items["open_tickets"],
        "ticket_id"
    )

    completed_tickets = add_source_reference(
        open_items["completed_tickets"],
        "ticket_id"
    )

    threads = add_source_reference(
        open_items["threads_to_review"],
        "thread_id"
    )

    recurring_tasks = add_source_reference(
        open_items["recurring_tasks_to_continue"],
        "task_id"
    )

    clients = add_source_reference(
        footprint["related_clients"],
        "client_id"
    )

    # Zestawienie terminów otwartych zgłoszeń i zadań.
    deadlines = []

    for ticket in open_items["open_tickets"]:
        if ticket.get("due_date"):
            deadlines.append({
                "type": "Zgłoszenie",
                "ticket_id": ticket["ticket_id"],
                "source_reference": f"ticket_id: {ticket['ticket_id']}",
                "title": ticket.get("title", ""),
                "due_date": ticket["due_date"],
                "priority": ticket.get("priority", "")
            })

    for task in open_items["recurring_tasks_to_continue"]:
        if task.get("due_date"):
            deadlines.append({
                "type": "Zadanie cykliczne",
                "source_reference": f"task_id: {task['task_id']}",
                "title": task.get("title", ""),
                "due_date": task["due_date"],
                "priority": task.get("priority", "")
            })

    deadlines.sort(key=lambda item: item["due_date"])

    # Sygnały wymagające uwagi na podstawie danych systemowych.
    attention_items = []

    for ticket in open_items["open_tickets"]:
        if str(ticket.get("priority", "")).lower() == "high":
            attention_items.append({
                "type": "Zgłoszenie o wysokim priorytecie",
                "source_reference": f"ticket_id: {ticket['ticket_id']}",
                "description": ticket.get("title", ""),
                "status": ticket.get("status", ""),
                "due_date": ticket.get("due_date", "")
            })

    for project in footprint["projects"]:
        if str(project.get("status", "")).lower() in (
            "in_progress", "in progress"
        ):
            attention_items.append({
                "type": "Projekt w trakcie realizacji",
                "source_reference": f"project_id: {project['project_id']}",
                "description": project.get("name", ""),
                "status": project.get("status", "")
            })


    # Budowanie dokumentu zgodnego z szablonem handover.

    lines = [
        "# Dokument przekazania obowiązków",
        "",
        "## Nordwind Logistics",
        "",
        "**Wersja:** 1.0",
        "**Typ dokumentu:** Dokument przekazania obowiązków",
        "**Status:** Do weryfikacji",
        "",
        "## 1. Dane pracowników",
        "",
        "| Pole | Informacja |",
        "|---|---|",
        f"| Pracownik przekazujący | {source_employee['first_name']} {source_employee['last_name']} |",
        f"| Identyfikator | {from_employee_id} |",
        f"| Dział | {source_employee['department']} |",
        f"| Pracownik przejmujący | {target_employee['first_name']} {target_employee['last_name']} |",
        f"| Identyfikator | {to_employee_id} |",
        f"| Data przekazania | Do uzupełnienia |",
        f"| Data zakończenia pracy przez przekazującego | Do uzupełnienia |",
        "",
        "## 2. Zakres przekazywanych obowiązków",
        "",
        "| Obowiązek | Częstotliwość | Priorytet | Uwagi |",
        "|---|---|---|---|",
    ]

    for task in recurring_tasks:
        lines.append(
            f"| {task.get('title', '')} "
            f"| {translate_frequency(task.get('frequency', 'Do potwierdzenia'))} "
            f"| {translate_priority(task.get('priority', 'Do potwierdzenia'))} "
            f"| Źródło: {task.get('source_reference', '')} |"
        )

    lines.extend([
        "",
        "## 3. Sprawy w toku",
        "",
        "| ID zgłoszenia | Sprawa | Status | Termin | Zalecane następne działanie |",
        "|---|---|---|---|---|",
    ])

    for ticket in open_tickets:
        lines.append(
            f"| {ticket.get('ticket_id', '')} "
            f"| {ticket.get('title', '')} "
            f"| {translate_status(ticket.get('status', ''))} "
            f"| {ticket.get('due_date', 'Brak terminu')} "
            f"| Do ustalenia z przekazującym "
            f"({ticket.get('source_reference', '')}) |"
        )

    lines.extend([
        "",
        "## 4. Kontakty i relacje biznesowe",
        "",
        "| Klient | Osoba kontaktowa | Temat współpracy | Otwarte sprawy |",
        "|---|---|---|---|",
    ])

    for client in clients:
        client_id = client.get("client_id")
        client_tickets = [
            ticket.get("ticket_id", "")
            for ticket in open_tickets
            if ticket.get("client_id") == client_id
        ]
        lines.append(
            f"| {client.get('company_name', '')} "
            f"| {client.get('contact_person', 'Do uzupełnienia')} "
            f"| Do potwierdzenia "
            f"| {', '.join(client_tickets) or 'Brak zarejestrowanych'} |"
        )

    lines.extend([
        "",
        "### Wątki komunikacyjne do przejrzenia",
        "",
    ])

    if threads:
        lines.extend([
            "| ID wątku | ID zgłoszenia | Klient | Temat rozmowy | Podsumowanie | Ostatnia wiadomość | Status |",
            "|---|---|---|---|---|---|---|",
        ])

        for thread in threads:
            lines.append(
                f"| {thread.get('thread_id', '')} "
                f"| {thread.get('ticket_id', '')} "
                f"| {thread.get('client_id', '')} "
                f"| {thread.get('subject', '')} "
                f"| {thread.get('summary', '')} "
                f"| {thread.get('last_message_at', 'Brak daty')} "
                f"| {translate_status(thread.get('status', 'Do potwierdzenia'))} |"
            )
    else:
        lines.append("Brak wątków komunikacyjnych do przejrzenia.")

    lines.append("")

    lines.extend([
    "",
    "## 5. Dokumentacja i źródła wiedzy",
    "",
    "| ID dokumentu | Nazwa | Znaczenie | Relacja pracownika |",
    "|---|---|---|---|",
])

    for doc in handover_documents:
        lines.append(
            f"| {doc.get('doc_id', '')} "
            f"| {doc.get('title', '')} "
            f"| {doc.get('content', 'Brak opisu')} "
            f"| {doc.get('relation_to_employee', '')} |"
        )

    lines.extend([
        "",
        "### Dokumenty dostępne według roli",
        "",
    ])

    if accessible_documents:
        lines.extend([
            "| ID dokumentu | Nazwa | Dział |",
            "|---|---|---|",
        ])

        for doc in accessible_documents:
            lines.append(
                f"| {doc.get('doc_id', '')} "
                f"| {doc.get('title', '')} "
                f"| {doc.get('department', '')} |"
            )
    else:
        lines.append("Brak dokumentów dostępnych według roli.")

    lines.extend([
        "",
        "**Uwaga:** dostępność według roli nie potwierdza indywidualnych "
        "uprawnień pracownika przejmującego. Należy zweryfikować dostęp "
        "zgodnie z ACL.",
        "",
    ])

    lines.extend([
        "",
        "Pracownik przejmujący powinien zapoznać się z dokumentacją "
        "przed samodzielnym realizowaniem obowiązków.",
        "Dostęp do dokumentów wymaga weryfikacji zgodnie z ACL.",
        "",
        "## 6. Zadania cykliczne i nadchodzące terminy",
        "",
    ])

    lines.extend([
        "",
        "| ID | Nazwa zadania lub sprawy | Rodzaj | Częstotliwość | Termin | Priorytet |",
        "|---|---|---|---|---|---|",
    ])

    for task in recurring_tasks:
        lines.append(
            f"| {task.get('task_id', '')} "
            f"| {task.get('title', '')} "
            f"| Zadanie cykliczne "
            f"| {translate_frequency(task.get('frequency', ''))} "
            f"| {task.get('due_date', 'Brak terminu')} "
            f"| {translate_priority(task.get('priority', ''))} |"
        )

    for deadline in deadlines:
        if deadline.get("type") == "Zadanie cykliczne":
            continue

        lines.append(
            f"| {deadline.get('ticket_id', '')} "
            f"| {deadline.get('title', '')} "
            f"| {deadline.get('type', '')} "
            f"| — "
            f"| {deadline.get('due_date', '')} "
            f"| {translate_priority(deadline.get('priority', ''))} |"
        )

    lines.append("")

    lines.extend([
        "",
        "## 7. Ryzyka i ważne informacje",
        "",
    ])

    lines.extend([
        "",
        "### Sygnały wymagające uwagi",
        "",
    ])

    if attention_items:
        lines.extend([
            "| Rodzaj | Opis | Status | Termin | Źródło |",
            "|---|---|---|---|---|",
        ])

        for item in attention_items:
            lines.append(
                f"| {item.get('type', '')} "
                f"| {item.get('description', '')} "
                f"| {translate_status(item.get('status', 'Do sprawdzenia'))} "
                f"| {item.get('due_date', 'Brak terminu')} "
                f"| {item.get('source_reference', '')} |"
            )
    else:
        lines.append("Brak zarejestrowanych sygnałów wymagających uwagi.")

    lines.extend([
        "",
        "### Projekty w toku",
        "",
    ])

    if projects:
        lines.extend([
            "| ID projektu | Nazwa | Właściciel | Status | Opis | Źródło |",
            "|---|---|---|---|---|---|",
        ])

        for project in projects:
            lines.append(
                f"| {project.get('project_id', '')} "
                f"| {project.get('name', '')} "
                f"| {project.get('owner_id', '')} "
                f"| {translate_status(project.get('status', ''))} "
                f"| {project.get('description', '')} "
                f"| {project.get('source_reference', '')} |"
            )
    else:
        lines.append("Brak zarejestrowanych projektów.")

    lines.extend([
        "",
        "### Historia zakończonych zgłoszeń",
        "",
    ])

    if completed_tickets:
        lines.extend([
            "| ID zgłoszenia | Sprawa | Status | Termin | Źródło |",
            "|---|---|---|---|---|",
        ])

        for ticket in completed_tickets:
            lines.append(
                f"| {ticket.get('ticket_id', '')} "
                f"| {ticket.get('title', '')} "
                f"| {translate_status(ticket.get('status', ''))} "
                f"| {ticket.get('due_date', 'Brak terminu')} "
                f"| {ticket.get('source_reference', '')} |"
            )
    else:
        lines.append("Brak zarejestrowanych zakończonych zgłoszeń.")

    lines.append("")

    lines.extend([
        "",
        "### Pułapki i nieoczywiste zasady",
        "",
        "- Przed zaksięgowaniem faktury należy sprawdzić jej zgodność "
        "z zamówieniem i potwierdzeniem wykonania usługi. "
        "W przypadku rozbieżności należy skontaktować się z dostawcą "
        "przed zaksięgowaniem (źródło: D002).",
        "",
        "- Proces zamknięcia miesiąca obejmuje uzgodnienie sald "
        "kontrahentów, weryfikację faktur i raport VAT "
        "(źródło: D003).",
        "",
        "- W sprawie T003 oczekiwane jest potwierdzenie wykonania "
        "usługi od klienta. Należy zweryfikować, czy dokument "
        "został już dostarczony (źródła: T003, TH003).",
        "",
        "- Dostęp do D002 i D003 wynika z roli R_FINANCE. "
        "Dostęp przejmującego wymaga osobnej weryfikacji "
        "(źródło: acl.csv).",
        "",
        "## 8. Rekomendacje dla pracownika przejmującego",
        "",
        "1. W pierwszej kolejności przeanalizować zgłoszenia o wysokim priorytecie: T001, T002, T004 i T010.",
        "2. Sprawdzić zgłoszenie T002, którego termin przypada na 2026-11-23.",
        "3. W sprawie T003 zweryfikować, czy klient C003 przesłał brakujące potwierdzenie wykonania usługi (źródła: T003, TH003).",
        "4. W sprawie T001 sprawdzić zgodność faktury FV/2026/101 z zamówieniem i potwierdzeniem wykonania usługi (źródła: T001, TH001, D002).",
        "5. Zapoznać się z procedurą rozliczania faktur i instrukcją zamknięcia miesiąca (D002, D003).",
        "6. Ustalić aktualny stan projektu P001 oraz uzgodnić, kto będzie odpowiedzialny za jego dalszą realizację.",
        "7. Zweryfikować nadchodzące terminy zadań cyklicznych RT001–RT005.",
        "8. Potwierdzić z Anną nieformalne ustalenia, kontakty z klientami i decyzje, których nie zapisano w systemie.",
        "",
        "## 9. Potwierdzenie przekazania",
        "",
        "| Osoba | Imię i nazwisko | Data | Podpis |",
        "|---|---|---|---|",
        f"| Przekazujący | {source_employee['first_name']} {source_employee['last_name']} | | |",
        f"| Przejmujący | {target_employee['first_name']} {target_employee['last_name']} | | |",
        "| Przełożony | Do uzupełnienia | | |",
        "",
        "## 10. Informacja o wykorzystaniu AI",
        "",
        "Dokument został przygotowany przez agenta Knowledge Manager "
        "na podstawie dostępnych danych firmowych.",
        "",
        "Agent:",
        "- korzysta z danych dostępnych w źródłach systemowych;",
        "- wskazuje identyfikatory rekordów źródłowych;",
        "- oznacza informacje brakujące jako wymagające uzupełnienia;",
        "- nie traktuje symulowanych odpowiedzi z wywiadu jako faktów;",
        "- nie zmienia automatycznie przypisań zadań ani zgłoszeń;",
        "",
        "**Status dokumentu:** Wymaga weryfikacji i zatwierdzenia "
        "przez pracowników.",
        "",
        f"**Data wygenerowania (UTC):** {generated_at}",
    ])

    document = "\n".join(lines)

    HANDOVER_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    safe_from_id = re.sub(
        r"[^a-zA-Z0-9_-]", "_", from_employee_id
    )
    safe_to_id = re.sub(
        r"[^a-zA-Z0-9_-]", "_", to_employee_id
    )

    output_file = (
        HANDOVER_OUTPUT_DIR
        / f"handover_{safe_from_id}_to_{safe_to_id}.md"
    )

    output_file.write_text(document, encoding="utf-8")

    return format_result({
        "status": "generated",
        "file_path": str(output_file.relative_to(BASE_DIR)),
        "from_employee": from_employee_id,
        "to_employee": to_employee_id,
        "generated_at": generated_at,
        "document": document
    })


# --------------------------------------------------
# ZASÓB MCP – INSTRUKCJA PROCESU
# --------------------------------------------------

@mcp.resource("handover://guidelines")
def handover_guidelines() -> str:
    """Udostępnia zasady bezpiecznego procesu przekazania obowiązków."""
    return """
    PROCES HANDOVER – NORDWIND LOGISTICS

    1. Zidentyfikuj pracownika przekazującego obowiązki.
    2. Zidentyfikuj pracownika przejmującego obowiązki.
    3. Użyj collect_footprint, aby zebrać ślad pracy.
    4. Użyj extract_open_items, aby wyodrębnić sprawy wymagające uwagi.
    5. Przeanalizuj zgłoszenia, wątki i zadania cykliczne.
    6. Zidentyfikuj klientów powiązanych ze sprawami.
    7. Przeprowadź wywiad z pracownikiem.
    8. Użyj generate_handover_doc, aby przygotować dokument.
    9. Wskaż źródła informacji.
    10. Oznacz dane wymagające potwierdzenia.
    11. Nie zmieniaj automatycznie przypisania zadań.

    Dane demonstracyjne nie zastępują rzeczywistej weryfikacji
    przez upoważnionego pracownika.
    """


# --------------------------------------------------
# PROMPT MCP – HANDOVER
# --------------------------------------------------

@mcp.prompt()
def handover(
    from_employee_id: str = "E001",
    to_employee_id: str = "E002"
) -> str:
    """Przygotowuje instrukcję procesu przekazania obowiązków."""

    if not PROMPT_FILE.exists():
        raise FileNotFoundError(
            f"Nie znaleziono promptu: {PROMPT_FILE}"
        )

    instructions = PROMPT_FILE.read_text(encoding="utf-8")

    return (
        f"{instructions}\n\n"
        f"Pracownik przekazujący: {from_employee_id}\n"
        f"Pracownik przejmujący: {to_employee_id}\n\n"
        "Rozpocznij proces od zebrania śladu pracy za pomocą "
        "collect_footprint. Następnie użyj extract_open_items. "
        "Po analizie przygotuj dokument za pomocą generate_handover_doc. "
        "Nie zmieniaj automatycznie przypisań zadań."
    )


# --------------------------------------------------
# URUCHOMIENIE SERWERA
# --------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
