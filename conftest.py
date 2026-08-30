# -*- coding: utf-8 -*-
"""pytest agrega automaticamente el directorio de este archivo a sys.path,
asi que `import scoping`, `import permisos`, etc. ya funcionan sin nada
mas. pipeline/ NO es un paquete (los scripts ahi adentro se importan con
sys.path.insert() a mano, mismo patron que ya usan entre si) -- se agrega
ese directorio tambien para que los tests puedan hacer
`import athena_client`, `import historial_cambios` igual que el resto de
la app."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline"))
