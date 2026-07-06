import argparse
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"))
import _bootstrap  # noqa: F401, E402

from pycallgraph2 import PyCallGraph, Config, GlobbingFilter
from pycallgraph2.output import GraphvizOutput

# -------------------- ARGUMENTS --------------------
parser = argparse.ArgumentParser(description="Generate call graph for a Python module using PyCallGraph2.")
parser.add_argument('--input', required=True,                                           help="Name of the Python module containing MeetingApp")
parser.add_argument('--filetype', choices=['dot', 'png', 'svg', 'pdf'], default='svg',  help="Output file type")
parser.add_argument('--calls',  type=lambda x: x.lower() == 'true',     default=False,  help="Show calls in node labels")
parser.add_argument('--time',   type=lambda x: x.lower() == 'true',     default=False,  help="Show execution time in node labels")

args = parser.parse_args()
input_module_name = args.input[:-3] if args.input.endswith(".py") else args.input
filetype = args.filetype
SHOW_CALLS = args.calls
SHOW_TIME = args.time

# -------------------- IMPORT MODULE --------------------
input_module = importlib.import_module(input_module_name)
MeetingApp = getattr(input_module, 'MeetingApp')

# -------------------- CONFIGURATION --------------------
config = Config()
config.trace_filter = GlobbingFilter(
    include=[f'{input_module_name}.*'],
    exclude=['__main__', '*.py', 'tkinter.*', 'pymongo.*', 'json.*']
)
config.trace_calls = SHOW_CALLS
config.trace_return = SHOW_CALLS


# -------------------- CUSTOM OUTPUT --------------------
class MyGraphvizOutput(GraphvizOutput):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.node_label_func = self.my_label_func
        self.edge_label_func = self.my_edge_label_func

    def _get_stat_value(self, obj, attrs):
        """Helper to extract integer/float values from Stat objects."""
        if isinstance(obj, (int, float)):
            return obj
        for attr in attrs:
            if hasattr(obj, attr):
                return getattr(obj, attr)
        return 0

    def my_edge_label_func(self, edge):
        if not SHOW_CALLS:
            return ""

        calls = getattr(edge, 'calls', 'N/A')
        value = self._get_stat_value(calls, ['value', 'calls', 'count'])
        return str(value)

    def my_label_func(self, node):
        label = node.name.replace(f"{input_module_name}.", "")

        if SHOW_CALLS:
            calls = getattr(node, 'calls', 'N/A')
            val = self._get_stat_value(calls, ['value', 'calls', 'count'])
            label += f"\\ncalls: {val}"

        if SHOW_TIME:
            time_obj = getattr(node, 'time', 0)
            val = self._get_stat_value(time_obj, ['value', 'time', 'total_time'])
            try:
                val = float(val)
            except (ValueError, TypeError):
                val = 0.0
            label += f"\\ntime: {val:.6f}s"

        return label

    def generate(self):
        if not self.processor:
            self.processor = self.create_post_processor()
            self.processor.process()

        # Remove __main__ node and cluster group
        if hasattr(self.processor, 'func_count') and '__main__' in self.processor.func_count:
            del self.processor.func_count['__main__']

        if hasattr(self.processor, 'func_groups') and '__main__' in self.processor.func_groups:
            del self.processor.func_groups['__main__']

        return super().generate()

    def edge(self, edge, attr):
        # Filter out arrows starting from __main__
        if getattr(edge, 'src_func', '') == '__main__':
            return ""
        return super().edge(edge, attr)


# -------------------- EXECUTION --------------------
output_filename = f'callgraph.{filetype}'
graphviz = MyGraphvizOutput()
graphviz.tool = 'dot'
graphviz.output_type = filetype
graphviz.output_file = output_filename


def main():
    app = MeetingApp()
    app.mainloop()


with PyCallGraph(output=graphviz, config=config):
    main()

print(f"Call graph saved to {output_filename}")