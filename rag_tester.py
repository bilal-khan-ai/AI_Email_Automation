import os
import sys
import logging
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import print as rprint
from rich.markdown import Markdown
from config import Config
from modules.vector_db import VectorDatabase
from modules.openai_agent import OpenAIAgent

# Mute noisy logs for clean screenshots
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("chromadb").setLevel(logging.WARNING)
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

console = Console()

def main():
    console.print(Panel.fit(
        "[bold white]Antigravity[/bold white] [bold blue]AI Email Automation - RAG Pipeline Tester[/bold blue]", 
        subtitle="Internal Testing Mode v2.0",
        border_style="blue"
    ))
    
    # 1. Initialize Components
    try:
        with console.status("[bold green]Initializing RAG components (ChromaDB + OpenAI)..."):
            # Experience DB (Past support emails)
            experience_db = VectorDatabase(
                Config.CHROMA_DB_PATH,
                Config.COLLECTION_NAME
            )
            
            # Documentation DB (BookStack)
            documentation_db = VectorDatabase(
                Config.BOOKSTACK_DB_PATH,
                Config.BOOKSTACK_COLLECTION
            )
            
            # OpenAI Agent
            ai = OpenAIAgent(Config.OPENAI_API_KEY)
            if not ai.authenticate():
                console.print("[red]Error: OpenAI authentication failed. Check your API key in .env[/red]")
                return
    except Exception as e:
        console.print(f"[red]Initialization Error: {e}[/red]")
        return

    # 2. Get User Input
    console.print("\n[bold yellow]Step 1: Input Test Email Content[/bold yellow]")
    console.print("[dim]Simulate a new incoming customer request similar to a resolved ticket.[/dim]")
    
    subject = console.input("[cyan]Subject: [/cyan]")
    body = console.input("[cyan]Body (Multi-line text): [/cyan]")
    
    if not subject or not body:
        console.print("[red]Subject and Body are required for testing![/red]")
        return

    query = f"Subject: {subject}\n\nBody: {body}"

    # 3. Perform RAG Search
    with console.status("[bold green]Querying Semantic Knowledge Bases..."):
        # Search Documentation
        doc_results = documentation_db.search_similar(query, top_k=Config.TOP_K_RESULTS)
        
        # Search Experience (Past Cases)
        exp_results = experience_db.search_similar(query, top_k=Config.TOP_K_RESULTS)

    # 4. Display Retrieval Evidence
    console.print("\n[bold yellow]Step 2: Retrieval Evidence (RAG Extraction)[/bold yellow]")
    
    # Documentation Table
    doc_table = Table(title="[bold green]Authoritative Documentation Found[/bold green]", show_header=True, header_style="bold magenta", expand=True)
    doc_table.add_column("Rank", style="dim", width=5)
    doc_table.add_column("Source/Section", width=40)
    doc_table.add_column("Similarity Score", justify="right")
    
    for i, res in enumerate(doc_results, 1):
        meta = res.get('metadata', {})
        source = f"{meta.get('book', 'N/A')} > {meta.get('section', 'N/A')}"
        sim = 1.0 - res.get('distance', 0.0)
        doc_table.add_row(str(i), source, f"{sim:.1%}")
    
    console.print(doc_table)

    # Experience Table
    exp_table = Table(title="[bold blue]Past Support Experience Found (Historical Cases)[/bold blue]", show_header=True, header_style="bold cyan", expand=True)
    exp_table.add_column("Rank", style="dim", width=5)
    exp_table.add_column("Subject Line", width=50)
    exp_table.add_column("Status", justify="center")
    exp_table.add_column("Similarity Score", justify="right")
    
    for i, res in enumerate(exp_results, 1):
        meta = res.get('metadata', {})
        subj = meta.get('subject', 'N/A')
        if len(subj) > 47: subj = subj[:47] + "..."
        
        # Color coding status
        status_val = meta.get('is_resolved')
        status = "[bold green]RESOLVED[/bold green]" if status_val else "[bold yellow]ONGOING[/bold yellow]"
        
        sim = 1.0 - res.get('distance', 0.0)
        exp_table.add_row(str(i), subj, status, f"{sim:.1%}")
    
    console.print(exp_table)

    # 5. Generate AI Response
    console.print("\n[bold yellow]Step 3: Generating AI Response (GPT-5-mini Pipeline)[/bold yellow]")
    
    # Mock message thread for the agent
    mock_messages = [{
        'sender': 'customer@example.com',
        'body_text': body,
        'subject': subject,
        'timestamp': '2026-05-06 12:00:00',
        'is_internal': False
    }]
    
    with console.status("[bold green]Reasoning and Generating Response..."):
        ai_response, _ = ai.generate_response(
            messages=mock_messages,
            documentation_context=doc_results,
            experience_context=exp_results,
            intent="QUERY",
            summary="Manual RAG validation test"
        )

    if ai_response:
        # Heuristic to separate Internal Reasoning from the actual draft
        # GPT-5-mini often follows the '1. INTERNAL REASONING' instruction literally
        
        display_content = ai_response
        reasoning_content = ""
        
        # Look for the end of reasoning or start of response
        # We check for common separators or keywords
        separators = ["---", "════", "FINAL RESPONSE", "RESPONSE:", "Email Draft:"]
        split_point = -1
        
        # If it has internal reasoning, try to isolate it
        if "INTERNAL REASONING" in ai_response.upper():
            for sep in separators:
                idx = ai_response.find(sep, ai_response.find("INTERNAL REASONING"))
                if idx != -1:
                    split_point = idx + len(sep)
                    break
            
            if split_point != -1:
                reasoning_content = ai_response[:split_point].strip()
                display_content = ai_response[split_point:].strip()
        
        # Handle HTML tags - the terminal doesn't render <p>, <br>, etc. well
        # We'll do a simple conversion for the screenshot
        import re
        clean_response = display_content
        clean_response = re.sub(r'<(p|br|div|hr|li)[^>]*>', '\n', clean_response) # Newlines for block tags
        clean_response = re.sub(r'<[^>]*>', '', clean_response) # Strip all other tags
        clean_response = clean_response.replace("&nbsp;", " ").strip()
        
        # Display Reasoning (dimmed)
        if reasoning_content:
            console.print(Panel(
                reasoning_content,
                title="[dim]Internal Reasoning (Heuristic Extract)[/dim]",
                border_style="dim",
                padding=(0, 1)
            ))

        # Display the Final Draft
        console.print(Panel(
            clean_response,
            title="[bold green]AI Generated Support Draft[/bold green]",
            subtitle=f"[dim]Tokens: {len(ai_response.split()) * 1.3:.0f} approx | RAG-Augmented Output[/dim]",
            border_style="green",
            padding=(1, 2)
        ))
        
        # Final safety check: if it's still suspiciously short, print raw
        if len(clean_response) < 20:
             console.print("\n[bold red]Note:[/bold red] The cleaned response seems too short. Printing raw output for verification:")
             console.print("-" * 40)
             console.print(ai_response)
             console.print("-" * 40)
    else:
        console.print("[red]Critical Error: AI engine failed to return a response.[/red]")

    console.print("\n[bold white on blue] TESTING COMPLETE [/bold white on blue]")
    console.print("[dim]The results above are ready for screenshotting as testing evidence.[/dim]\n")

if __name__ == "__main__":
    main()
