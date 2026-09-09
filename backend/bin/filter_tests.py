#!/usr/bin/env python3
import json
import argparse
import sys
from datetime import datetime

def parse_iso_datetime(dt_str):
    if not dt_str:
        return datetime.min
    try:
        clean_str = dt_str.replace("Z", "+00:00")
        return datetime.fromisoformat(clean_str)
    except Exception:
        return datetime.min

def main():
    parser = argparse.ArgumentParser(
        description="Identifica i test da eliminare in base alle statistiche di esecuzione, della data di prima esecuzione e della durata."
    )
    parser.add_argument("file_path", help="Percorso al file JSON con le statistiche dei test")
    parser.add_argument(
        "--percentage", "-p", type=float, default=None,
        help="Percentuale di test da eliminare (es. 30 per il 30%%)"
    )
    parser.add_argument(
        "--max-duration", "-m", type=float, default=None,
        help="Durata massima desiderata per l'intera suite di test"
    )
    parser.add_argument(
        "--unit", choices=["seconds", "minutes"], default="seconds",
        help="Unità di misura per --max-duration (default: seconds)"
    )
    
    args = parser.parse_args()
    
    if args.percentage is None and args.max_duration is None:
        parser.error("È necessario specificare almeno una tra le opzioni --percentage (-p) o --max-duration (-m).")

    if args.percentage is not None and not (0 < args.percentage <= 100):
        print("Errore: la percentuale deve essere un valore compreso tra 0 e 100.", file=sys.stderr)
        sys.exit(1)
        
    try:
        with open(args.file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Errore nella lettura del file: {e}", file=sys.stderr)
        sys.exit(1)
        
    tests = []
    total_suite_duration = 0.0
    for test_name, stats in data.items():
        runs = stats.get("runs", 0)
        failures = stats.get("failures", 0)
        seconds = stats.get("seconds", 0.0)
        
        # Assumiamo la presenza di 'first_run' (con fallback a 'last_run' o min se non trovato)
        first_run_str = stats.get("first_run") or stats.get("last_run") or ""
        first_run_dt = parse_iso_datetime(first_run_str)
        
        total_suite_duration += seconds
        
        tests.append({
            "test": test_name,
            "failures": failures,
            "first_run": first_run_dt,
            "runs": runs,
            "seconds": seconds
        })
        
    # Gerarchia di ordinamento per identificare i candidati prioritari da ELIMINARE:
    # 1. failures (crescente -> prima quelli con 0 fallimenti)
    # 2. first_run timestamp (decrescente -> prima quelli creati/eseguiti RECENTEMENTE, preservando quelli "antichi")
    # 3. runs (decrescente -> prima quelli eseguiti più volte)
    # 4. seconds (decrescente -> prima quelli che durano di più)
    tests.sort(key=lambda x: (
        x["failures"],
        -x["first_run"].timestamp(),
        -x["runs"],
        -x["seconds"]
    ))
    
    total_tests = len(tests)
    
    # 1. Calcolo limite percentuale
    limit_by_percentage = total_tests
    if args.percentage is not None:
        limit_by_percentage = int(round(total_tests * (args.percentage / 100.0)))
        
    # 2. Calcolo limite durata massima
    limit_by_duration = total_tests
    if args.max_duration is not None:
        target_max_sec = args.max_duration * 60.0 if args.unit == "minutes" else args.max_duration
        time_to_cut = total_suite_duration - target_max_sec
        
        if time_to_cut <= 0:
            limit_by_duration = 0
        else:
            cum_time = 0.0
            count = 0
            for t in tests:
                cum_time += t["seconds"]
                count += 1
                if cum_time >= time_to_cut:
                    break
            limit_by_duration = count
            
    # Criterio "quale arriva prima"
    num_to_delete = min(limit_by_percentage, limit_by_duration)
    
    to_delete = tests[:num_to_delete]
    
    for t in to_delete:
        print(t["test"])

if __name__ == "__main__":
    main()
