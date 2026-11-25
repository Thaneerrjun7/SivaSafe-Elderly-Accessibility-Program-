import ast
import os

class AttrVisitor(ast.NodeVisitor):
    def __init__(self):
        self.assigned = set()
        self.read = set()
        self.current_class = None

    def visit_ClassDef(self, node):
        old_class = self.current_class
        self.current_class = node.name
        self.generic_visit(node)
        self.current_class = old_class

    def visit_Attribute(self, node):
        if isinstance(node.value, ast.Name) and node.value.id == 'self':
            if isinstance(node.ctx, ast.Store):
                self.assigned.add((self.current_class, node.attr))
            elif isinstance(node.ctx, ast.Load):
                self.read.add((self.current_class, node.attr))
        self.generic_visit(node)

with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'ui.py'), 'r', encoding='utf-8') as f:
    tree = ast.parse(f.read())

visitor = AttrVisitor()
visitor.visit(tree)

missing = visitor.read - self.assigned if hasattr(visitor, 'assigned') else visitor.read - visitor.assigned
for cls, attr in sorted(missing):
    if attr not in ['__init__', '__class__']:
        print(f"Class {cls} reads self.{attr} but never assigns it!")
