"""Lossless, opt-in text-response encoding. Project schemas remain unchanged."""
import copy
import re
import string


class CompactDirectorWire:
    version = 1

    def __init__(self, context, schema):
        self.ids = {}
        self.names = {}
        # Only identity fields establish aliases. Never replace arbitrary prose.
        def identities(value):
            if isinstance(value, dict):
                if isinstance(value.get('id'),str) and isinstance(value.get('name'),str):
                    self.names.setdefault(value['id'],value['name'])
                for key, item in value.items():
                    if key in ('id', 'characterId') and isinstance(item, str) and item:
                        self.ids.setdefault(item, 'P' + str(len(self.ids)))
                    identities(item)
            elif isinstance(value, list):
                for item in value: identities(item)
        identities(context)
        # A source string that already equals an alias would be ambiguous.
        strings = set()
        def collect(value):
            if isinstance(value, str): strings.add(value)
            elif isinstance(value, dict):
                for key, item in value.items(): strings.add(key); collect(item)
            elif isinstance(value, list):
                for item in value: collect(item)
        collect(context); collect(schema)
        while any(re.search(r'(?<!\w)'+re.escape(alias)+r'(?!\w)',text) for alias in self.ids.values() for text in strings):
            self.ids = {key: '_' + value for key, value in self.ids.items()}
        self.reverse_ids = {value: key for key, value in self.ids.items()}
        names = set()
        def fields(node):
            names.update(node.get('properties', {}))
            for child in node.get('properties', {}).values(): fields(child)
            if isinstance(node.get('items'), dict): fields(node['items'])
            for key in ('anyOf', 'oneOf', 'allOf'):
                for child in node.get(key, []): fields(child)
        fields(schema)
        alphabet = string.ascii_lowercase + string.ascii_uppercase
        self.keys = {key: alphabet[i] if i < len(alphabet) else 'k' + str(i)
                     for i, key in enumerate(sorted(names))}
        self.original_schema = schema
        self.schema = self.encode_schema(schema)
        self.context = self.encode_values(context)

    def encode_values(self, value):
        if isinstance(value, str): return self.ids.get(value, value)
        if isinstance(value, list): return [self.encode_values(v) for v in value]
        if isinstance(value, dict):
            return {self.ids.get(k, k): self.encode_values(v) for k, v in value.items()}
        return value

    def encode_schema(self, node):
        result = copy.deepcopy(node)
        if 'enum' in node: result['enum'] = self.encode_values(node['enum'])
        if 'properties' in node:
            result['properties'] = {}
            for name, child in node['properties'].items():
                encoded = self.encode_schema(child)
                encoded['description'] = name + '. ' + child.get('description', '')
                result['properties'][self.keys[name]] = encoded
            result['required'] = [self.keys[name] for name in node.get('required', [])]
        if isinstance(node.get('items'), dict): result['items'] = self.encode_schema(node['items'])
        for key in ('anyOf', 'oneOf', 'allOf'):
            if key in node: result[key] = [self.encode_schema(child) for child in node[key]]
        return result

    def decode(self, value, node=None, field=None):
        node = self.original_schema if node is None else node
        if 'anyOf' in node:
            from director_provider import validate_schema
            for branch in node['anyOf']:
                try:validate_schema(value,self.encode_schema(branch))
                except ValueError:continue
                return self.decode(value,branch,field)
            raise ValueError('Compact director value matches no allowed schema branch.')
        if isinstance(value, dict) and 'properties' in node:
            expected = {self.keys[name] for name in node['properties']}
            if set(value) - expected: raise ValueError('Unexpected compact director field.')
            return {name: self.decode(value[self.keys[name]], child,name)
                    for name, child in node['properties'].items() if self.keys[name] in value}
        if isinstance(value, list):
            return [self.decode(item, node.get('items', {}),field) for item in value]
        if isinstance(value, str):
            if field in ('id','characterId','characters','sceneId','chapterId') or 'enum' in node:
                return self.reverse_ids.get(value,value)
            # Short IDs in free composition/action prose must become names so
            # image prompts and human editors keep understandable role bindings.
            for alias,identity in self.reverse_ids.items():
                value=re.sub(r'(?<!\w)'+re.escape(alias)+r'(?!\w)',lambda _:self.names.get(identity,identity),value)
            return value
        return value
