"""A minimal, independent reader of Quake BSP29 / BSP2 files: lump sizes and point contents.

It shares no code with the compilers it is used to judge. Hull 0 is walked through the node
tree, hulls 1 and 2 through the clip nodes, exactly as the engine does.
"""

import struct

CONTENTS = {-1: "EMPTY", -2: "SOLID", -3: "WATER", -4: "SLIME", -5: "LAVA", -6: "SKY"}
LUMPS = ("entities", "planes", "textures", "vertexes", "visibility", "nodes", "texinfo", "faces", "lighting",
         "clipnodes", "leafs", "marksurfaces", "edges", "surfedges", "models")


class Bsp:
    def __init__(self, path):
        with open(path, "rb") as f:
            self.data = f.read()
        ident = self.data[:4]
        if ident == b"BSP2":
            self.format = "BSP2"
        elif struct.unpack_from("<i", self.data, 0)[0] == 29:
            self.format = "BSP29"
        else:
            raise ValueError("%s is neither BSP29 nor BSP2 (first bytes %r)" % (path, ident))
        self.lumps = {}
        for i, name in enumerate(LUMPS):
            offset, length = struct.unpack_from("<ii", self.data, 4 + 8 * i)
            if offset < 0 or length < 0 or offset + length > len(self.data):
                raise ValueError("%s: lump %s lies outside the file" % (path, name))
            self.lumps[name] = (offset, length)
        self.node_size, self.leaf_size, self.clip_size = (44, 44, 12) if self.format == "BSP2" else (24, 28, 8)

    def size(self, name):
        return self.lumps[name][1]

    def count(self, name):
        per = {"planes": 20, "nodes": self.node_size, "leafs": self.leaf_size, "clipnodes": self.clip_size, "models": 64}[name]
        return self.lumps[name][1] // per

    def _plane(self, index):
        nx, ny, nz, dist = struct.unpack_from("<4f", self.data, self.lumps["planes"][0] + 20 * index)
        return (nx, ny, nz), dist

    def headnodes(self, model=0):
        return struct.unpack_from("<4i", self.data, self.lumps["models"][0] + 64 * model + 36)

    def contents(self, hull, point, model=0):
        """Contents number at `point` (hull 0: point hull; 1: player; 2: large monsters)."""
        num = self.headnodes(model)[hull]
        if hull == 0:
            base = self.lumps["nodes"][0]
            while num >= 0:
                if self.format == "BSP2":
                    plane, front, back = struct.unpack_from("<iii", self.data, base + 44 * num)
                else:
                    plane, front, back = struct.unpack_from("<ihh", self.data, base + 24 * num)
                normal, dist = self._plane(plane)
                d = sum(n * p for n, p in zip(normal, point)) - dist
                num = front if d >= 0 else back
            return struct.unpack_from("<i", self.data, self.lumps["leafs"][0] + self.leaf_size * (-1 - num))[0]
        base = self.lumps["clipnodes"][0]
        while num >= 0:
            if self.format == "BSP2":
                plane, front, back = struct.unpack_from("<iii", self.data, base + 12 * num)
            else:
                plane, front, back = struct.unpack_from("<iHH", self.data, base + 8 * num)
                # BSP29 stores children as unsigned 16-bit: the top 16 values are contents
                front = front - 65536 if front >= 0xFFF0 else front
                back = back - 65536 if back >= 0xFFF0 else back
            normal, dist = self._plane(plane)
            d = sum(n * p for n, p in zip(normal, point)) - dist
            num = front if d >= 0 else back
        return num

    def contents_name(self, hull, point, model=0):
        value = self.contents(hull, point, model)
        return CONTENTS.get(value, str(value))
