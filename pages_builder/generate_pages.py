# coding=utf-8

import os
import pystache
import yaml
import json
import sys
from subprocess import call
from distutils import dir_util
from collections import OrderedDict
from template_handler import TemplateHandler
from asset_compiler import AssetCompiler
from jinja2 import Environment, FileSystemLoader, Template
from pygments import highlight
from pygments.lexers import HtmlLexer, DjangoLexer
from pygments.formatters import HtmlFormatter


# preserve key order when parsing YAML – http://stackoverflow.com/a/21048064/147318

def dict_representer(dumper, data):
    return dumper.represent_dict(data.items())


def dict_constructor(loader, node):
    return OrderedDict(loader.construct_pairs(node))


yaml.add_representer(OrderedDict, dict_representer)
yaml.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, dict_constructor)


class ToolkitEnvironment(Environment):
    """Override join_path() to strip out 'toolkit/' string from template paths."""
    def join_path(self, template, parent):
        if template.startswith('toolkit/'):
            template = template.replace('toolkit/', '')
        return super(ToolkitEnvironment, self).join_path(template, parent)


class Styleguide_publisher(object):
    "publish a styleguide for the toolkit"

    pages = []

    def __init__(self):
        self.repo_root = os.path.abspath(os.path.join(
            os.path.dirname(__file__), ".."
        ))
        self.pages_dirname = os.path.join(
            self.repo_root, "pages_builder/pages"
        )
        self.output_dirname = os.path.join(self.repo_root, "pages")
        self.template_dir = self.get_template_folder()
        self.template_view = self.get_template_view()
        self.asset_compiler = AssetCompiler()
        self.render_pages()
        self.compile_assets(os.path.join(
            self.repo_root, "pages_builder/assets/scss"
        ))
        self.copy_javascripts()
        self.copy_images()

    def get_version(self):
        data = json.load(open(os.path.join(self.repo_root, "package.json")))
        return data["version"]

    def get_template_folder(self):
        template_handler = TemplateHandler()
        if template_handler.needs_update():
            template_handler.update_template()
        return template_handler.get_folder()

    def get_template_view(self):
        base_template = open(
            os.path.join(
                self.template_dir, "views/layouts/govuk_template.html"
            ), "r"
        ).read()
        return base_template

    def render_pages(self):

        print("\nCREATING PAGES from " + self.pages_dirname + " \n")

        if os.path.isdir(self.output_dirname) is False:
            print("★ Creating " + self.output_dirname)
            os.mkdir(self.output_dirname)

        for root, dirs, files in os.walk(self.pages_dirname):
            for dir in dirs:
                output_dir = self.__get_output_dir(dir)
                if os.path.isdir(output_dir) is False:
                    print("★ Creating " + output_dir)
                    os.mkdir(output_dir)
                else:
                    print("✔ Found " + output_dir)

            for file in files:
                if self.__is_yaml(file):
                    self.render_page(root, file)

    def parameters_example(self, template_subfolder, template_name, parameters):
        parameters = parameters.copy()
        parameters_template_file = os.path.join(
            self.repo_root,
            "pages_builder/parameters_template.html"
        )

        parameters_template = Template(
            open(parameters_template_file).read()
        )

        if "title" in parameters:
            # title is a parameter reserved for naming the pattern
            # in the documentation
            parameters.pop("title", None)

        presented_parameters = {
            key: json.dumps(value, indent=4)
            for key, value in parameters.items()
        }

        return parameters_template.render(
            {
                "parameters": presented_parameters,
                "file": os.path.join("toolkit", template_subfolder, template_name) + ".html"
            }
        )

    def render_include(self, include_file_name, data):
        template_file = os.path.join(self.repo_root, "pages_builder", "includes", include_file_name)
        template = open(template_file, "r").read()
        return pystache.render(template, data)

    def render_page(self, root, file):
        input_file = os.path.join(root, file)
        output_file = self.__get_page_filename(input_file)
        partial = yaml.safe_load(open(input_file, "r").read())
        url_root = os.getenv("ROOT_DIRECTORY") or ""
        print("\n  " + input_file)
        # for index pages, we want to render variables in the content
        # - add version number from package.json to the main index page
        # - add urlRoot to the nested 'index.html' pages
        if 'index.html' in output_file:
            partial['content'] = pystache.render(
                partial['content'], {"version": self.get_version()}
            )
        else:
            partial['pageHeading'] = partial['pageTitle']
            partial['pageTitle'] = (
                partial['pageTitle'] +
                " - Digital Marketplace frontend toolkit"
            )
            examples_html_only = 'examples_html_only' in partial
            if "examples" in partial:
                template_name, template_extension = os.path.splitext(file)
                template_subfolder = root.replace(self.pages_dirname, "").strip("/")
                env = ToolkitEnvironment(
                    loader=FileSystemLoader(os.path.join(self.repo_root, "toolkit/templates"))
                )
                env.add_extension('jinja2.ext.with_')

                template_file = os.path.join(template_subfolder, template_name + ".html")
                template = None
                # not all of our examples have template files (`_lists.scss`, for example)
                if os.path.isfile(os.path.join(self.repo_root, "toolkit/templates", template_file)):
                    template = env.get_template(template_file)
                    has_template = True
                else:
                    has_template = False
                examples = []
                for index, example in enumerate(partial["examples"]):
                    grid = partial.get('grid')
                    # If a pattern doesn't have a template in the toolkit, use the jinja from the example
                    if not has_template:
                        template = env.from_string(example)
                    if isinstance(example, dict):
                        # if the example has some html it needs to be displayed,
                        # cache it and remove from the parameters example
                        surrounding_html = example.get('surrounding_html', None)
                        if surrounding_html:
                            del example['surrounding_html']

                        example_template = self.parameters_example(template_subfolder, template_name, example)
                        example_markup = template.render(example)
                        if surrounding_html:
                            example_markup = env.from_string(surrounding_html).render({ 'example': example_markup })
                        # set a grid if specified. Example-level grids will overwrite the one for the page
                        grid = example.get('grid', partial.get('grid'))
                    else:
                        example_template = example
                        example_markup = env.from_string(example_template).render({})

                    examples.append({
                        "markup": example_markup,
                        "highlighted_markup": highlight(example_markup, HtmlLexer(), HtmlFormatter(noclasses=True)),
                        "grid": grid
                    })
                    if template and not examples_html_only:
                        examples[-1].update(
                            {"parameters": highlight(example_template, DjangoLexer(), HtmlFormatter(noclasses=True))}
                        )
                partial_data = {
                    "examples": examples,
                    "pageTitle": partial['pageTitle'],
                    "pageDescription": partial.get('pageDescription'),
                    "pageHeading": partial['pageHeading'],
                    "templateFile": template_file,
                    "urlRoot": url_root
                }

                if "grid" in partial:
                    partial_data['grid'] = partial['grid']

                partial['content'] = self.render_include(
                    os.path.join(self.repo_root, "pages_builder", "includes", "content.html"),
                    partial_data
                )

        partial['head'] = self.render_include(
            os.path.join(self.repo_root, "pages_builder", "includes", "head.html"),
            {"url_root": url_root}
        )
        bodyEnd = partial['bodyEnd'] if "bodyEnd" in partial else ""
        partial['bodyEnd'] = self.render_include(
            os.path.join(self.repo_root, "pages_builder", "includes", "bodyEnd.html"),
            {
                "url_root": url_root,
                "bodyEnd": bodyEnd
            }
        )
        page_render = pystache.render(self.template_view, partial)
        print("▸ " + output_file)
        open(output_file, "wb+").write(page_render.encode('utf-8'))

    def compile_assets(self, folder):
        print("\nCOMPILING ASSETS from " + folder + "\n")
        self.asset_compiler.compile(folder)

    def copy_javascripts(self):
        print("\nCOPYING JAVASCRIPTSn")
        print("Created files:\n\n")
        copied_scripts = []
        copied_scripts += dir_util.copy_tree("toolkit/javascripts", "pages/public/javascripts")
        copied_scripts += dir_util.copy_tree(
            "pages_builder/assets/javascripts", "pages/public/javascripts/"
        )
        copied_scripts += dir_util.copy_tree(
            "node_modules/govuk_frontend_toolkit/javascripts",
            "pages/public/javascripts/govuk_frontend_toolkit/"
        )
        print("\n".join(copied_scripts))
        print("★ Done")

    def copy_images(self):
        print("\nCOPYING IMAGES\n")
        dir_util.copy_tree("toolkit/images", "pages/public/images")
        print("★ Done")

    def __get_output_dir(self, directory):
        return os.path.join(self.output_dirname, directory)

    def __get_page_filename(self, filename):
        filename = filename.replace(self.pages_dirname, self.output_dirname)
        filename_parts = os.path.splitext(filename)
        html_version = filename_parts[0] + ".html"
        return os.path.join(html_version)

    def __is_yaml(self, file):
        filename, extension = os.path.splitext(file)
        return extension == ".yml"

if __name__ == "__main__":
    styleguide_publisher = Styleguide_publisher()
