#!/usr/bin/env python3
"""
JSON to CSV Vulnerability Data Converter

Converts JSON vulnerability scan results to CSV format with specified field mapping.
"""

import argparse
import csv
import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Any


def setup_logging(verbose: bool) -> None:
    """Configure logging based on verbosity level."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(levelname)s: %(message)s'
    )


def parse_arguments() -> argparse.Namespace:
    """Parse and validate command line arguments."""
    parser = argparse.ArgumentParser(
        description='Convert JSON vulnerability scan results to CSV format',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
CSV Output Fields (in order):
  cve_id        - CVE identifier
  package       - Component/package name
  package_ve    - Package version
  rh_severity   - Red Hat severity rating
  rh_cvss       - CVSS score
  container     - Container name
  container_tag - Container tag
  advisory      - Advisory identifier

Example usage:
  python json_to_csv.py -i output.json -c myapp:v1.2.3
  python json_to_csv.py -i output.json -c myapp:v1.2.3 -o vulnerabilities.csv -v
        """
    )
    
    parser.add_argument(
        '-i', '--input',
        required=True,
        type=str,
        help='Input JSON file path (required)'
    )
    
    parser.add_argument(
        '-o', '--output',
        type=str,
        help='Output CSV file path (defaults to input filename with .csv extension)'
    )
    
    parser.add_argument(
        '-c', '--container',
        required=True,
        type=str,
        help='Container identifier in format <container_name>:<container_tag> (required)'
    )
    
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    
    args = parser.parse_args()
    
    # Validate input file exists
    if not os.path.isfile(args.input):
        parser.error(f"Input file does not exist: {args.input}")
    
    # Validate container format
    if ':' not in args.container:
        parser.error("Container must be in format <container_name>:<container_tag>")
    
    container_parts = args.container.split(':', 1)
    if not container_parts[0] or not container_parts[1]:
        parser.error("Both container name and tag must be non-empty")
    
    # Set default output filename if not provided
    if not args.output:
        input_path = Path(args.input)
        args.output = str(input_path.with_suffix('.csv'))
    
    return args


def load_json_data(file_path: str) -> Dict[str, Any]:
    """Load and validate JSON data from file."""
    logging.info(f"Loading JSON data from: {file_path}")
    
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in file {file_path}: {e}")
    except Exception as e:
        raise IOError(f"Error reading file {file_path}: {e}")
    
    # Validate expected JSON structure
    if not isinstance(data, dict):
        raise ValueError("JSON root must be an object")
    
    if 'result' not in data:
        raise ValueError("JSON must contain 'result' field")
    
    if not isinstance(data['result'], dict):
        raise ValueError("'result' field must be an object")
    
    if 'vulnerabilities' not in data['result']:
        raise ValueError("JSON must contain 'result.vulnerabilities' field")
    
    if not isinstance(data['result']['vulnerabilities'], list):
        raise ValueError("'result.vulnerabilities' field must be an array")
    
    logging.info(f"Successfully loaded {len(data['result']['vulnerabilities'])} vulnerabilities")
    return data


def extract_vulnerability_data(vulnerabilities: List[Dict[str, Any]], container_name: str, container_tag: str) -> List[Dict[str, str]]:
    """Extract and map vulnerability data to CSV format."""
    logging.info("Extracting vulnerability data...")
    
    csv_data = []
    
    for vuln in vulnerabilities:
        if not isinstance(vuln, dict):
            logging.warning("Skipping non-object vulnerability entry")
            continue
        
        # Map JSON fields to CSV fields with safe extraction
        csv_row = {
            'cve_id': str(vuln.get('cveId', '')),
            'package': str(vuln.get('componentName', '')),
            'package_ve': str(vuln.get('componentVersion', '')),
            'rh_severity': str(vuln.get('cveSeverity', '')),
            'rh_cvss': str(vuln.get('cveCVSS', '')),
            'container': container_name,
            'container_tag': container_tag,
            'advisory': str(vuln.get('advisoryId', ''))
        }
        
        csv_data.append(csv_row)
    
    logging.info(f"Extracted {len(csv_data)} vulnerability records")
    return csv_data


def write_csv_data(csv_data: List[Dict[str, str]], output_path: str) -> None:
    """Write vulnerability data to CSV file."""
    logging.info(f"Writing CSV data to: {output_path}")
    
    if not csv_data:
        logging.warning("No data to write to CSV file")
        return
    
    # Define field order as specified in requirements
    fieldnames = [
        'cve_id',
        'package', 
        'package_ve',
        'rh_severity',
        'rh_cvss',
        'container',
        'container_tag',
        'advisory'
    ]
    
    try:
        with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            # Write header row
            writer.writeheader()
            
            # Write data rows
            writer.writerows(csv_data)
        
        logging.info(f"Successfully wrote {len(csv_data)} records to {output_path}")
        
    except Exception as e:
        raise IOError(f"Error writing CSV file {output_path}: {e}")


def main() -> int:
    """Main application entry point."""
    try:
        # Parse command line arguments
        args = parse_arguments()
        
        # Setup logging
        setup_logging(args.verbose)
        
        # Load and validate JSON data
        json_data = load_json_data(args.input)
        
        # Parse container information
        container_name, container_tag = args.container.split(':', 1)
        logging.info(f"Using container: {container_name}:{container_tag}")
        
        # Extract vulnerability data
        csv_data = extract_vulnerability_data(json_data['result']['vulnerabilities'], container_name, container_tag)
        
        # Write CSV output
        write_csv_data(csv_data, args.output)
        
        print(f"Successfully converted {len(csv_data)} vulnerabilities from {args.input} to {args.output}")
        return 0
        
    except Exception as e:
        logging.error(f"Conversion failed: {e}")
        return 1


if __name__ == '__main__':
    sys.exit(main())
