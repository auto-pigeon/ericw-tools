/*
    Copyright (C) 1996-1997  Id Software, Inc.
    Copyright (C) 1997       Greg Lewis

    This program is free software; you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation; either version 2 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program; if not, write to the Free Software
    Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA

    See file, 'COPYING', for details.
*/
// portals.c

#include <qbsp/brush.hh>
#include <qbsp/portals.hh>

#include <qbsp/map.hh>
#include <qbsp/brushbsp.hh>
#include <qbsp/qbsp.hh>
#include <qbsp/outside.hh>
#include <qbsp/tree.hh>
#include <common/log.hh>
#include <atomic>
#include <common/prtfile.hh>

#include "tbb/task_group.h"
#include "common/vectorutils.hh"

contentflags_t ClusterContents(const node_t *node)
{
    /* Pass the leaf contents up the stack */
    if (auto *leafdata = node->get_leafdata())
        return leafdata->contents;

    auto *nodedata = node->get_nodedata();
    return ClusterContents(nodedata->children[0]).cluster_contents(ClusterContents(nodedata->children[1]));
}

/*
=============
Portal_VisFlood

Returns true if the portal is empty or translucent, allowing
the PVS calculation to see through it.
The nodes on either side of the portal may actually be clusters,
not leafs, so all contents should be ored together
=============
*/
bool Portal_VisFlood(const portal_t *p)
{
    if (!p->onnode) {
        return false; // to global outsideleaf
    }

    contentflags_t contents0 = ClusterContents(p->nodes[0]);
    contentflags_t contents1 = ClusterContents(p->nodes[1]);

    // Check per-game visibility
    return contentflags_t::portal_can_see_through(contents0, contents1);
}

/*
===============
Portal_EntityFlood

The entity flood determines which areas are
"outside" on the map, which are then filled in.
Flowing from side s to side !s
===============
*/
bool Portal_EntityFlood(const portal_t *p, int32_t s)
{
    if (!p->nodes[0]->is_leaf() || !p->nodes[1]->is_leaf()) {
        FError("Portal_EntityFlood: not a leaf");
    }

    // can never cross to a solid
    if (p->nodes[0]->get_leafdata()->contents.is_solid() || p->nodes[1]->get_leafdata()->contents.is_solid()) {
        return false;
    }

    // can flood through everything else
    return true;
}

/*
=============
AddPortalToNodes
=============
*/
static void AddPortalToNodes(portal_t *p, node_t *front, node_t *back)
{
    if (p->nodes[0] || p->nodes[1])
        FError("portal already included");

    p->nodes[0] = front;
    p->next[0] = front->portals;
    front->portals = p;

    p->nodes[1] = back;
    p->next[1] = back->portals;
    back->portals = p;
}

/*
================
MakeHeadnodePortals

The created portals will face the global outside_node
================
*/
std::list<buildportal_t> MakeHeadnodePortals(tree_t &tree)
{
    int i, j, n;
    std::array<buildportal_t, 6> portals{};
    qplane3d bplanes[6];

    // pad with some space so there will never be null volume leafs
    aabb3d bounds = tree.bounds.grow(SIDESPACE);

    tree.outside_node.make_leaf();
    tree.outside_node.get_leafdata()->contents = contentflags_t::make(EWT_VISCONTENTS_SOLID);
    tree.outside_node.portals = nullptr;

    // create 6 portals forming a cube around the bounds of the map.
    // these portals will have `outside_node` on one side, and headnode on the other.
    for (i = 0; i < 3; i++)
        for (j = 0; j < 2; j++) {
            n = j * 3 + i;

            auto &p = portals[n];

            qplane3d &pl = bplanes[n] = {};

            if (j) {
                pl.normal[i] = -1;
                pl.dist = -bounds[j][i];
            } else {
                pl.normal[i] = 1;
                pl.dist = bounds[j][i];
            }
            bool side = p.plane.set_plane(pl, true);

            p.winding = BaseWindingForPlane<winding_t>(pl);
            if (side) {
                p.nodes = {&tree.outside_node, tree.headnode};
            } else {
                p.nodes = {tree.headnode, &tree.outside_node};
            }
        }

    // clip the basewindings by all the other planes
    for (i = 0; i < 6; i++) {
        winding_t &w = portals[i].winding;

        for (j = 0; j < 6; j++) {
            if (j == i)
                continue;

            if (auto w2 = w.clip_front(bplanes[j], qbsp_options.epsilon.value(), true)) {
                w = std::move(*w2);
            } else {
                FError("portal winding clipped away");
            }
        }
    }

    // move into std::list
    return {std::make_move_iterator(portals.begin()), std::make_move_iterator(portals.end())};
}

//============================================================================

/*
================
BaseWindingForNode

Creates a winding from the given node plane, clipped by all parent nodes.
================
*/
constexpr double BASE_WINDING_EPSILON = 0.001;
constexpr double SPLIT_WINDING_EPSILON = 0.001;

/*
==================
WindingMinWidth

The least distance across a convex winding, in its own plane: for each edge, the farthest any point
lies from the edge's line; the least of those. (For a convex polygon the minimum width is attained with
an edge flush against one of the two parallel supporting lines.)
==================
*/
static double WindingMinWidth(const winding_t &w)
{
    double best = std::numeric_limits<double>::infinity();
    for (size_t i = 0; i < w.size(); i++) {
        const qvec3d edge = w[(i + 1) % w.size()] - w[i];
        const double length = qv::length(edge);
        if (length < NORMAL_EPSILON) {
            continue;
        }
        const qvec3d dir = edge / length;
        double farthest = 0;
        for (size_t j = 0; j < w.size(); j++) {
            farthest = std::max(farthest, qv::length(qv::cross(dir, w[j] - w[i])));
        }
        best = std::min(best, farthest);
    }
    return best;
}

/*
==================
KeepTinyPortalForFaces

WindingIsTiny drops a portal with fewer than three edges longer than EDGE_LENGTH_EPSILON (0.2): "crunched out of
existence by the vertex snapping". In this compiler a drawn face IS a portal winding (FaceFromPortal), so a
dropped tiny portal between air and a solid is an undrawn strip of the solid's face: SCULPT's tessellation-16
patch lost a cap fragment 7 units long and 0.135 wide that way (NEW_328), the narrow chamfers 0.03-0.07 wide
likewise. The drop is also what keeps hairline gaps between brushes from leaking and VIS away from slivers, and
that must not change.

So a tiny portal is kept only to draw its face -- a draw-only portal: it is pushed down to the leaves like any
other, but it is never linked into a node's portal list, so the outside and area floods, VIS, the portal file
and leaf bounds read exactly the portals they read without it.

It is kept only when the output can carry the face it makes, which two later passes decide: the vertex weld
(EmitVertex: vertices closer than POINT_EQUAL_EPSILON are one) and the T-junction pass (PointOnEdge: a vertex
within DEFAULT_ON_EPSILON of an edge is put INTO that edge). A winding the weld crunches to fewer than three
vertices or to no area is dropped, and so is one no wider than DEFAULT_ON_EPSILON anywhere: the T-junction pass
cannot keep its two long edges apart, and threads one's vertices into the other or into the next face along
(measured: the 0.06-wide sides of a thin skin and of a water film came out with zero or reversed normals, their
own or a neighbour's). Such a winding was not drawn before either.
==================
*/
static bool KeepTinyPortalForFaces(const winding_t &w)
{
    std::vector<qvec3d> welded;
    for (auto &point : w) {
        if (welded.empty() || qv::distance(welded.back(), point) >= POINT_EQUAL_EPSILON) {
            welded.push_back(point);
        }
    }
    while (welded.size() > 1 && qv::distance(welded.front(), welded.back()) < POINT_EQUAL_EPSILON) {
        welded.pop_back();
    }
    if (welded.size() < 3) {
        return false;
    }
    const winding_t kept(welded.begin(), welded.end());
    return kept.area() > ZERO_TRI_AREA_EPSILON && WindingMinWidth(kept) > DEFAULT_ON_EPSILON;
}

/*
==================
ClipPortalWinding

winding_t::clip with `epsilon`, except where the epsilon alone decides that the whole winding lies on one
side while a finer clip finds a substantial part of it on the other side.

The epsilon is a distance from the plane. When the plane meets the winding at a small dihedral angle
theta (two nearly coplanar walls: a gently curved surface made of many brushes), every point within
epsilon / theta of the intersection line is "on" -- 0.001 / 0.001 rad is a strip 1 unit wide. If the
rest of the winding lies on one side (or the whole winding is "on"), the strip is moved to that side
with it: the portal then bounds the wrong leaf there (typically a solid brush instead of the air in
front of the wall), and the face that portal would have made is never emitted. Leaf contents, and so
collision, are not affected; the wall just has an undrawn strip.

The finer clip's verdict is taken only for pieces that are substantial: not tiny and at least
EDGE_LENGTH_EPSILON wide. When both pieces are substantial the winding is split there; when only the
piece on the side the epsilon left empty is, the whole winding goes to that side. Otherwise the
epsilon's verdict stands, so no sliver, and no piece that the callers would drop as tiny, is made.

The same strip goes astray when the epsilon does split the winding but one vertex is "on" only by the
epsilon while lying well away from the intersection line (EpsilonMovesSplitLine): the split line then
runs through that vertex, and the triangle between it and the true intersection -- up to epsilon / theta
wide -- is handed to the wrong child (Q2_012A organic: a wall triangle 74 units long and 0.37 wide went to
the air leaf in front of the next brush's wall, so it bounded air on both sides and was never drawn). There
too the finer clip's verdict is taken when both of its pieces are substantial.
==================
*/
constexpr double PORTAL_FINE_EPSILON = NORMAL_EPSILON;

static bool PortalPieceIsSubstantial(const std::optional<winding_t> &w)
{
    return w && !WindingIsTiny(*w) && WindingMinWidth(*w) >= EDGE_LENGTH_EPSILON;
}

/*
==================
EpsilonMovesSplitLine

True when some vertex of `w` is "on" `plane` only by the epsilon (farther than PORTAL_FINE_EPSILON, not farther
than `epsilon`) and lies at least EDGE_LENGTH_EPSILON from the line where the two planes meet. Such a vertex is
put on both sides, so the split line is drawn through it instead of along the intersection: the triangle between
the two lines -- as wide as the vertex is far from the intersection, distance / sin(theta) -- goes to the wrong
side. For two planes at a large angle that triangle is never wider than the epsilon itself and this is false.
==================
*/
static bool EpsilonMovesSplitLine(const winding_t &w, const qplane3d &plane, double epsilon)
{
    const double sin_theta = qv::length(qv::cross(w.plane().normal, plane.normal));

    for (auto &point : w) {
        const double d = std::fabs(plane.distance_to(point));
        if (d > PORTAL_FINE_EPSILON && d <= epsilon && d >= EDGE_LENGTH_EPSILON * sin_theta) {
            return true;
        }
    }
    return false;
}

static twosided<std::optional<winding_t>> ClipPortalWinding(
    const winding_t &w, const qplane3d &plane, double epsilon, bool keepon)
{
    auto result = w.clip(plane, epsilon, keepon);

    const bool one_sided = result.front.has_value() != result.back.has_value();
    const bool two_sided = result.front.has_value() && result.back.has_value();

    if (!one_sided && !(two_sided && EpsilonMovesSplitLine(w, plane, epsilon))) {
        return result;
    }

    auto fine = w.clip(plane, PORTAL_FINE_EPSILON, keepon);
    const bool front = PortalPieceIsSubstantial(fine.front);
    const bool back = PortalPieceIsSubstantial(fine.back);

    if (front && back) {
        return fine;
    }
    if (one_sided && front && !result.front) {
        return {w.clone(), std::nullopt};
    }
    if (one_sided && back && !result.back) {
        return {std::nullopt, w.clone()};
    }

    return result;
}

static std::optional<winding_t> BaseWindingForNode(const node_t *node)
{
    std::optional<winding_t> w = BaseWindingForPlane<winding_t>(node->get_nodedata()->get_plane());

    // clip by all the parents
    for (auto *np = node->parent; np && w;) {

        auto clipped = ClipPortalWinding(*w, np->get_nodedata()->get_plane().get_plane(), BASE_WINDING_EPSILON, false);

        if (np->get_nodedata()->children[0] == node) {
            w = std::move(clipped.front);
        } else {
            w = std::move(clipped.back);
        }

        node = np;
        np = np->parent;
    }

    return w;
}

/*
==================
MakeNodePortal

create the new portal by taking the full plane winding for the cutting plane
and clipping it by all of parents of this node, as well as all the other
portals in the node.
==================
*/
static std::optional<buildportal_t> MakeNodePortal(
    node_t *node, portaltype_t type, const std::list<buildportal_t> &boundary_portals, portalstats_t &stats)
{
    auto w = BaseWindingForNode(node);

    // clip the portal by all the other portals in the node
    for (auto &p : boundary_portals) {
        if (!w) {
            break;
        }
        if (p.draw_only) {
            continue; // not a portal to anything but the face it draws: the winding is clipped as without it
        }
        qplane3d plane;

        if (p.nodes[0] == node) {
            plane = p.plane;
        } else if (p.nodes[1] == node) {
            plane = -p.plane;
        } else {
            Error("CutNodePortals_r: mislinked portal");
        }

        // fixme-brushbsp: magic number
        w = w->clip_front(plane, 0.1, false);
    }

    if (!w) {
        return std::nullopt;
    }

    bool draw_only = false;
    if (WindingIsTiny(*w)) {
        stats.c_tinyportals++;
        if (type != portaltype_t::TREE || !KeepTinyPortalForFaces(*w)) {
            return std::nullopt;
        }
        stats.c_drawonlyportals++;
        draw_only = true;
    }

    auto *nodedata = node->get_nodedata();

    buildportal_t new_portal{};
    new_portal.plane = nodedata->get_plane();
    new_portal.onnode = node;
    new_portal.winding = std::move(*w);
    new_portal.nodes = nodedata->children;
    new_portal.draw_only = draw_only;
    return std::move(new_portal);
}

/*
==============
SplitNodePortals

Move or split the portals that bound node so that the node's
children have portals instead of node.
==============
*/
static twosided<std::list<buildportal_t>> SplitNodePortals(
    const node_t *node, portaltype_t type, std::list<buildportal_t> boundary_portals, portalstats_t &stats)
{
    auto *nodedata = node->get_nodedata();

    const auto &plane = nodedata->get_plane();
    node_t *f = nodedata->children[0];
    node_t *b = nodedata->children[1];

    twosided<std::list<buildportal_t>> result;

    for (auto &p : boundary_portals) {
        // which side of p `node` is on
        planeside_t side;
        if (p.nodes[SIDE_FRONT] == node)
            side = SIDE_FRONT;
        else if (p.nodes[SIDE_BACK] == node)
            side = SIDE_BACK;
        else
            FError("CutNodePortals_r: mislinked portal");

        node_t *other_node = p.nodes[!side];
        p.nodes = {nullptr, nullptr};

        //
        // cut the portal into two portals, one on each side of the cut plane
        //
        auto [frontwinding, backwinding] = ClipPortalWinding(p.winding, plane.get_plane(), SPLIT_WINDING_EPSILON, true);

        // a draw-only portal (KeepTinyPortalForFaces) is split like any other but never folded into the other
        // child: each piece that can still be drawn goes to its own side
        auto keep_draw_only = [&](std::optional<winding_t> &piece, node_t *child, std::list<buildportal_t> &out) {
            if (!piece || !KeepTinyPortalForFaces(*piece)) {
                return;
            }
            buildportal_t kept{};
            kept.plane = p.plane;
            kept.onnode = p.onnode;
            kept.winding = std::move(*piece);
            kept.nodes = (side == SIDE_FRONT) ? twosided<node_t *>{child, other_node}
                                              : twosided<node_t *>{other_node, child};
            kept.draw_only = true;
            kept.fill_only = p.fill_only;
            out.push_back(std::move(kept));
        };

        // a tiny piece folded into the other child below: that child's portal covers it for the faces, but the leaf
        // on the piece's own side has no opening there any more. It is kept as a fill-only opening (never drawn, never
        // linked) when it is a winding KeepTinyPortalForFaces would keep, and only KeepThinLeavesOpen reads it
        auto keep_fill_only = [&](std::optional<winding_t> &piece, node_t *child, std::list<buildportal_t> &out) {
            if (type != portaltype_t::TREE || !piece || !KeepTinyPortalForFaces(*piece)) {
                return;
            }
            buildportal_t kept{};
            kept.plane = p.plane;
            kept.onnode = p.onnode;
            kept.winding = std::move(*piece);
            kept.nodes = (side == SIDE_FRONT) ? twosided<node_t *>{child, other_node}
                                              : twosided<node_t *>{other_node, child};
            kept.draw_only = true;
            kept.fill_only = true;
            out.push_back(std::move(kept));
            stats.c_fillonlyportals++;
        };

        if (p.draw_only) {
            keep_draw_only(frontwinding, f, result.front);
            keep_draw_only(backwinding, b, result.back);
            continue;
        }

        std::optional<winding_t> tinyfront, tinyback;

        if (frontwinding && WindingIsTiny(*frontwinding)) {
            tinyfront = std::move(frontwinding);
            frontwinding = {};
            stats.c_tinyportals++;
        }

        if (backwinding && WindingIsTiny(*backwinding)) {
            tinyback = std::move(backwinding);
            backwinding = {};
            stats.c_tinyportals++;
        }

        if (!frontwinding && !backwinding) { // tiny windings on both sides
            // nothing is folded into the other child, so the pieces are lost to the faces too unless kept to draw
            if (type == portaltype_t::TREE) {
                const size_t before = result.front.size() + result.back.size();
                keep_draw_only(tinyfront, f, result.front);
                keep_draw_only(tinyback, b, result.back);
                stats.c_drawonlyportals += result.front.size() + result.back.size() - before;
            }
            continue;
        }

        if (!frontwinding) {
            keep_fill_only(tinyfront, f, result.front);
            if (side == SIDE_FRONT)
                p.nodes = {b, other_node};
            else
                p.nodes = {other_node, b};

            result.back.push_back(std::move(p));
            continue;
        }
        if (!backwinding) {
            keep_fill_only(tinyback, b, result.back);
            if (side == SIDE_FRONT)
                p.nodes = {f, other_node};
            else
                p.nodes = {other_node, f};

            result.front.push_back(std::move(p));
            continue;
        }

        // the winding is split
        buildportal_t new_portal{};
        new_portal.plane = p.plane;
        new_portal.onnode = p.onnode;
        new_portal.nodes[0] = p.nodes[0];
        new_portal.nodes[1] = p.nodes[1];
        new_portal.winding = std::move(*backwinding);
        p.winding = std::move(*frontwinding);

        if (side == SIDE_FRONT) {
            p.nodes = {f, other_node};
            new_portal.nodes = {b, other_node};
        } else {
            p.nodes = {other_node, f};
            new_portal.nodes = {other_node, b};
        }

        result.front.push_back(std::move(p));
        result.back.push_back(std::move(new_portal));
    }

    return result;
}

/*
================
MakePortalsFromBuildportals
================
*/
void MakePortalsFromBuildportals(tree_t &tree, std::list<buildportal_t> &buildportals)
{
    tree.portals.reserve(buildportals.size());

    for (auto &buildportal : buildportals) {
        if (buildportal.draw_only) {
            // owned by the tree, never linked into a node's portal list (KeepTinyPortalForFaces)
            auto &kept = tree.draw_only_portals.emplace_back(std::make_unique<portal_t>());
            kept->plane = buildportal.plane;
            kept->onnode = buildportal.onnode;
            kept->winding = std::move(buildportal.winding);
            kept->nodes = buildportal.nodes;
            kept->fill_only = buildportal.fill_only;
            continue;
        }
        portal_t *new_portal = tree.create_portal();
        new_portal->plane = buildportal.plane;
        new_portal->onnode = buildportal.onnode;
        new_portal->winding = std::move(buildportal.winding);
        AddPortalToNodes(new_portal, buildportal.nodes[0], buildportal.nodes[1]);
    }
}

/*
================
CalcNodeBounds
================
*/
inline void CalcNodeBounds(node_t *node)
{
    // calc mins/maxs for both leafs and nodes
    node->bounds = aabb3d{};

    for (portal_t *p = node->portals; p;) {
        int s = (p->nodes[1] == node);
        for (auto &point : p->winding) {
            node->bounds += point;
        }
        p = p->next[s];
    }
}

static void CalcTreeBounds_r(node_t *node, logging::percent_clock &clock)
{
    if (node->is_leaf()) {
        clock();
        CalcNodeBounds(node);
    } else {
        auto *nodedata = node->get_nodedata();

        tbb::task_group g;
        g.run([&]() { CalcTreeBounds_r(nodedata->children[0], clock); });
        g.run([&]() { CalcTreeBounds_r(nodedata->children[1], clock); });
        g.wait();

        node->bounds = nodedata->children[0]->bounds + nodedata->children[1]->bounds;
    }

    if (node->bounds.mins()[0] >= node->bounds.maxs()[0]) {
        // logging::print("WARNING: {} without a volume\n", node->is_leaf ? "leaf" : "node");

        // fixme-brushbsp: added this to work around leafs with no portals showing up in "qbspfeatures.map" among other
        // test maps. Not sure if correct or there's another underlying problem.
        node->bounds = {node->parent->bounds.mins(), node->parent->bounds.mins()};
    }

    for (auto &v : node->bounds.mins()) {
        if (fabs(v) > qbsp_options.worldextent.value()) {
            logging::print("WARNING: {} with unbounded volume\n", node->is_leaf() ? "leaf" : "node");
            break;
        }
    }
}

/*
==================
ClipNodePortalToTree_r

Given portals which are connected to `node` on one side,
descends the tree, splitting the portals as needed until they are connected to leaf nodes.

The other side of the portals will remain untouched.
==================
*/
static std::list<buildportal_t> ClipNodePortalsToTree_r(
    node_t *node, portaltype_t type, std::list<buildportal_t> portals, portalstats_t &stats)
{
    if (portals.empty()) {
        return portals;
    }
    if (node->is_leaf() || (type == portaltype_t::VIS && node->get_nodedata()->detail_separator)) {
        return portals;
    }
    auto *nodedata = node->get_nodedata();

    auto boundary_portals_split = SplitNodePortals(node, type, std::move(portals), stats);

    auto front_fragments =
        ClipNodePortalsToTree_r(nodedata->children[0], type, std::move(boundary_portals_split.front), stats);
    auto back_fragments =
        ClipNodePortalsToTree_r(nodedata->children[1], type, std::move(boundary_portals_split.back), stats);

    std::list<buildportal_t> merged_result = std::move(front_fragments);
    merged_result.splice(merged_result.end(), back_fragments);
    return merged_result;
}

/*
==================
MakeTreePortals_r

Given the list of portals bounding `node`, returns the portal list for a fully-portalized `node`.
==================
*/
std::list<buildportal_t> MakeTreePortals_r(node_t *node, portaltype_t type, std::list<buildportal_t> boundary_portals,
    portalstats_t &stats, logging::percent_clock &clock)
{
    clock();

    if (node->is_leaf() || (type == portaltype_t::VIS && node->get_nodedata()->detail_separator)) {
        return boundary_portals;
    }

    // make the node portal before we move out the boundary_portals
    std::optional<buildportal_t> nodeportal = MakeNodePortal(node, type, boundary_portals, stats);

    // parallel part: split boundary_portals between the front and back, and obtain the fully
    // portalized front/back sides in parallel

    auto boundary_portals_split = SplitNodePortals(node, type, std::move(boundary_portals), stats);

    std::list<buildportal_t> result_portals_front, result_portals_back;

    auto *nodedata = node->get_nodedata();

    tbb::task_group g;
    g.run([&]() {
        result_portals_front =
            MakeTreePortals_r(nodedata->children[0], type, std::move(boundary_portals_split.front), stats, clock);
    });
    g.run([&]() {
        result_portals_back =
            MakeTreePortals_r(nodedata->children[1], type, std::move(boundary_portals_split.back), stats, clock);
    });
    g.wait();

    // sequential part: push the nodeportal down each side of the bsp so it connects leafs

    std::list<buildportal_t> result_portals_onnode;

    if (nodeportal) {
        // to start with, `nodeportal` is a portal between node->children[0] and node->children[1]

        // these portal fragments have node->children[1] on one side, and the leaf nodes from
        // node->children[0] on the other side
        std::list<buildportal_t> half_clipped =
            ClipNodePortalsToTree_r(nodedata->children[0], type, make_list(std::move(*nodeportal)), stats);

        result_portals_onnode = ClipNodePortalsToTree_r(nodedata->children[1], type, std::move(half_clipped), stats);
    }

    // all done, merge together the lists and return
    std::list<buildportal_t> merged_result = std::move(result_portals_front);
    merged_result.splice(merged_result.end(), result_portals_back);
    merged_result.splice(merged_result.end(), result_portals_onnode);
    return merged_result;
}

/*
==================
MakeTreePortals
==================
*/
void MakeTreePortals(tree_t &tree)
{
    logging::funcheader();

    FreeTreePortals(tree);

    auto headnodeportals = MakeHeadnodePortals(tree);

    {
        logging::percent_clock clock(tree.nodes.size());

        portalstats_t stats{};

        auto buildportals =
            MakeTreePortals_r(tree.headnode, portaltype_t::TREE, std::move(headnodeportals), stats, clock);

        MakePortalsFromBuildportals(tree, buildportals);
    }

    logging::header("CalcTreeBounds");

    logging::percent_clock clock;
    CalcTreeBounds_r(tree.headnode, clock);
    clock.print();

    struct tree_portal_stats_t : logging::stat_tracker_t
    {
        stat &portals = register_stat("tree portals");
    } stats;

    stats.portals.count = tree.portals.size();
    tree.portaltype = portaltype_t::TREE;
}

/*
=========================================================

FLOOD AREAS

=========================================================
*/

static void ApplyArea_r(node_t *node)
{
    if (auto *nodedata = node->get_nodedata()) {
        ApplyArea_r(nodedata->children[0]);
        ApplyArea_r(nodedata->children[1]);
        return;
    }

    node->get_leafdata()->area = map.c_areas;
}

static mapentity_t *AreanodeEntityForLeaf(node_t *node)
{
    // if detail cluster, search the children recursively
    if (auto *nodedata = node->get_nodedata()) {
        if (auto *child0result = AreanodeEntityForLeaf(nodedata->children[0]); child0result) {
            return child0result;
        }
        return AreanodeEntityForLeaf(nodedata->children[1]);
    }

    auto *leafdata = node->get_leafdata();
    for (auto &brush : leafdata->original_brushes) {
        if (brush->mapbrush->func_areaportal) {
            return brush->mapbrush->func_areaportal;
        }
    }
    return nullptr;
}

/*
=============
FloodAreas_r
=============
*/
static void FloodAreas_r(node_t *node)
{
    auto *leafdata = node->get_leafdata();
    Q_assert(leafdata);

    if (leafdata->contents.flags & EWT_INVISCONTENTS_AREAPORTAL) {
        // grab the func_areanode entity
        mapentity_t *entity = AreanodeEntityForLeaf(node);

        if (entity == nullptr) {
            logging::print("WARNING: areaportal contents in node, but no entity found {} -> {}\n", node->bounds.mins(),
                node->bounds.maxs());
            return;
        }

        // this node is part of an area portal;
        // if the current area has allready touched this
        // portal, we are done
        if (entity->portalareas[0] == map.c_areas || entity->portalareas[1] == map.c_areas)
            return;

        // note the current area as bounding the portal
        if (entity->portalareas[1]) {
            logging::print("WARNING: {}: areaportal touches > 2 areas\n  Entity Bounds: {} -> {}\n", entity->location,
                entity->bounds.mins(), entity->bounds.maxs());
            return;
        }

        if (entity->portalareas[0])
            entity->portalareas[1] = map.c_areas;
        else
            entity->portalareas[0] = map.c_areas;

        return;
    }

    if (leafdata->area)
        return; // already got it

    leafdata->area = map.c_areas;

    // propagate area assignment to descendants if we're a cluster
    // (unnecessary because we did a full tree portalization)
#if 0
    if (!node->is_leaf()) {
        ApplyArea_r(node);
    }
#endif

    int32_t s;

    for (portal_t *p = node->portals; p; p = p->next[s]) {
        s = (p->nodes[1] == node);
#if 0
		if (p->nodes[!s]->occupied)
			continue;
#endif
        if (!Portal_EntityFlood(p, s))
            continue;

        FloodAreas_r(p->nodes[!s]);
    }
}

/*
=============
FindAreas_r

Just decend the tree, and for each node that hasn't had an
area set, flood fill out from there
=============
*/
static void FindAreas_r(node_t *node)
{
    if (auto *nodedata = node->get_nodedata()) {
        FindAreas_r(nodedata->children[0]);
        FindAreas_r(nodedata->children[1]);
        return;
    }

    auto *leafdata = node->get_leafdata();

    if (leafdata->area)
        return; // already got it

    if (leafdata->contents.is_any_solid())
        return;

    if (!leafdata->occupied)
        return; // not reachable from an entity

    // area portals are always only flooded into, never
    // out of
    if (leafdata->contents.flags & EWT_INVISCONTENTS_AREAPORTAL)
        return;

    map.c_areas++;
    FloodAreas_r(node);
}

/**
 * Starting at `a`, find and return the shortest path to `b`.
 *
 * Reference:
 * https://en.wikipedia.org/wiki/Breadth-first_search#Pseudocode
 */
static std::list<node_t *> FindShortestPath(node_t *a, node_t *b, const std::function<bool(portal_t *)> &passable)
{
    std::list<node_t *> queue;
    std::unordered_set<node_t *> queue_set;
    std::unordered_map<node_t *, node_t *> parent;

    queue.push_back(a);
    queue_set.insert(a);

    while (!queue.empty()) {
        node_t *node = queue.front();
        queue.pop_front();

        if (node == b) {
            // reached target. now we just need to extract the path we took from the `parent` map.
            std::list<node_t *> result;
            for (node_t *n = b;; n = parent.at(n)) {
                result.push_front(n);
                if (n == a)
                    break;
            }
            return result;
        }

        // push neighbouring nodes onto the back of the queue,
        // if they're not already enqueued, and if the portal is passable
        int side;
        for (portal_t *portal = node->portals; portal; portal = portal->next[!side]) {
            side = (portal->nodes[0] == node);

            node_t *neighbour = portal->nodes[side];

            if (!passable(portal))
                continue;
            if (queue_set.find(neighbour) != queue_set.end())
                continue;

            // enqueue it
            queue.push_back(neighbour);
            queue_set.insert(neighbour);
            parent[neighbour] = node;
        }
    }

    // couldn't find a path
    return {};
}

using exit_t = std::tuple<portal_t *, node_t *>;

static void FindAreaPortalExits_R(node_t *n, std::unordered_set<node_t *> &visited, std::vector<exit_t> &exits)
{
    Q_assert(n->is_leaf());

    visited.insert(n);

    int s;
    for (portal_t *p = n->portals; p; p = p->next[!s]) {
        s = (p->nodes[0] == n);

        node_t *neighbour = p->nodes[s];

        // already visited?
        if (visited.find(neighbour) != visited.end())
            continue;

        // is this an exit?
        if (!(neighbour->get_leafdata()->contents.flags & EWT_INVISCONTENTS_AREAPORTAL) &&
            !neighbour->get_leafdata()->contents.is_solid()) {
            exits.emplace_back(p, neighbour);
            continue;
        }

        // valid edge to explore?
        // if this isn't an exit, don't leave AREAPORTAL
        if (!(neighbour->get_leafdata()->contents.flags & EWT_INVISCONTENTS_AREAPORTAL))
            continue;

        // continue exploding
        FindAreaPortalExits_R(neighbour, visited, exits);
    }
}

/**
 * DFS to find all portals leading out of the Q2_CONTENTS_AREAPORTAL leaf `n`, into non-solid leafs.
 * Returns all of the portals and corresponding "outside" leafs.
 */
static std::vector<exit_t> FindAreaPortalExits(node_t *n)
{
    std::unordered_set<node_t *> visited;
    std::vector<exit_t> exits;

    FindAreaPortalExits_R(n, visited, exits);

    return exits;
}

/**
 * Attempts to write a leak line showing how the two sides of the areaportal are reachable.
 */
static void DebugAreaPortalBothSidesLeak(node_t *node)
{
    std::vector<exit_t> exits = FindAreaPortalExits(node);

    if (exits.size() < 2) {
        logging::funcprint("WARNING: only found {} exits\n", exits.size());
        return;
    }

    auto [exit_portal0, exit_leaf0] = exits[0];

    // look for the other exit `i`, such that the shortest path between exit 0 and `i` is the longest.
    // this is to avoid picking two exits on the same side of the areaportal, which would not help
    // track down the leak.
    size_t longest_length = 0;
    std::list<node_t *> longest_path;

    for (size_t i = 1; i < exits.size(); ++i) {
        auto [exit_portal_i, exit_leaf_i] = exits[i];

        auto path = FindShortestPath(exit_leaf0, exit_leaf_i, [](portal_t *p) -> bool {
            if (!Portal_EntityFlood(p, 0))
                return false;

            // don't go back into an areaportal
            if ((p->nodes[0]->get_leafdata()->contents.flags & EWT_INVISCONTENTS_AREAPORTAL) ||
                (p->nodes[1]->get_leafdata()->contents.flags & EWT_INVISCONTENTS_AREAPORTAL))
                return false;

            return true;
        });

        if (path.size() > longest_length) {
            longest_length = path.size();
            longest_path = path;
        }
    }

    // write `longest_path` as the leak

    fs::path name = qbsp_options.bsp_path;
    name.replace_extension(fmt::format("areaportal_leak{}.pts", map.numareaportal_leaks));

    std::ofstream ptsfile(name);

    if (!ptsfile)
        FError("Failed to open {}: {}", name, strerror(errno));

    for (auto it = longest_path.begin();; ++it) {
        if (it == longest_path.end())
            break;

        auto next_it = it;
        next_it++;

        if (next_it == longest_path.end())
            break;

        WriteLeakTrail(ptsfile, (*it)->bounds.centroid(), (*next_it)->bounds.centroid());
    }

    logging::print("Wrote areaportal leak to {}\n", name);

    ++map.numareaportal_leaks;
}

/*
=============
SetAreaPortalAreas_r

Just decend the tree, and for each node that hasn't had an
area set, flood fill out from there
=============
*/
static void SetAreaPortalAreas_r(node_t *node)
{
    if (auto *nodedata = node->get_nodedata()) {
        SetAreaPortalAreas_r(nodedata->children[0]);
        SetAreaPortalAreas_r(nodedata->children[1]);
        return;
    }

    auto *leafdata = node->get_leafdata();

    if (leafdata->contents.flags != EWT_INVISCONTENTS_AREAPORTAL)
        return;

    if (leafdata->area)
        return; // already set

    // grab the func_areanode entity
    mapentity_t *entity = AreanodeEntityForLeaf(node);

    if (!entity) {
        logging::print("WARNING: areaportal missing for node: {} -> {}\n", node->bounds.mins(), node->bounds.maxs());
        return;
    }

    leafdata->area = entity->portalareas[0];
    if (!entity->portalareas[1]) {
        if (!entity->wrote_doesnt_touch_two_areas_warning) {
            entity->wrote_doesnt_touch_two_areas_warning = true;
            logging::print(
                "WARNING: {}: areaportal entity {} with targetname {} doesn't touch two areas\n  Node bounds: {} -> {}\n",
                entity->location, entity - map.entities.data(), entity->epairs.get("targetname"), node->bounds.mins(),
                node->bounds.maxs());
            DebugAreaPortalBothSidesLeak(node);
        }
        return;
    }
}

/*
=============
EmitAreaPortals

Mark each leaf with an area, bounded by CONTENTS_AREAPORTAL and
emit them.
=============
*/
void EmitAreaPortals(tree_t &tree)
{
    Q_assert(tree.portaltype == portaltype_t::TREE);

    logging::funcheader();

    map.bsp.dareaportals.emplace_back();
    map.bsp.dareas.emplace_back();

    // don't do anything else if we've leaked
    if (map.leakfile || map.antiregions.size() || map.region) {

        map.bsp.dareas.emplace_back();

        for (auto &e : map.entities) {
            e.areaportalnum = 0;

            if (e.epairs.get("classname") == "func_areaportal") {
                e.epairs.remove("style");
            }
        }

        return;
    }

    FindAreas_r(tree.headnode);
    SetAreaPortalAreas_r(tree.headnode);

    for (size_t i = 1; i <= map.c_areas; i++) {
        darea_t &area = map.bsp.dareas.emplace_back();
        area.firstareaportal = map.bsp.dareaportals.size();

        for (auto &e : map.entities) {

            if (!e.areaportalnum)
                continue;
            dareaportal_t dp = {};

            if (e.portalareas[0] == i) {
                dp.portalnum = e.areaportalnum;
                dp.otherarea = e.portalareas[1];
            } else if (e.portalareas[1] == i) {
                dp.portalnum = e.areaportalnum;
                dp.otherarea = e.portalareas[0];
            }

            size_t j = 0;

            for (; j < map.bsp.dareaportals.size(); j++) {
                if (map.bsp.dareaportals[j] == dp)
                    break;
            }

            if (j == map.bsp.dareaportals.size())
                map.bsp.dareaportals.push_back(dp);
        }

        area.numareaportals = map.bsp.dareaportals.size() - area.firstareaportal;
    }

    logging::stat_tracker_t area_stats;
    area_stats.register_stat("areas").count += map.c_areas;
    area_stats.register_stat("area portals").count += map.bsp.dareaportals.size();
}

//==============================================================

struct visible_faces_stats_t : logging::stat_tracker_t
{
    stat &sides_not_found = register_stat("sides not found (use -verbose to display)", false, true);
    stat &sides_visible = register_stat("sides visible");

    std::vector<polylib::winding_t> missing_portal_sides;
};

/*
============
FindNearerSideOneLeafAway

FindPortalSide found no brush of either leaf with a side on the portal, and textures it with the side whose normal is
nearest, up to 0.1 away. A leaf thinner than PLANESIDE_EPSILON cannot be split again (its caps are degenerate), so its
contents can come from a fragment of a brush that only reaches into part of it: a corner of the next cell of a
heightfield of nearly coplanar facets, left in the sliver between two of their planes (NEW_328A). The portal on such a
leaf lies on the surface of a brush ONE PORTAL AWAY -- the facet's own, behind the sliver -- and the nearest-normal side
of the leaf's own brushes is another brush's, several hundredths away: the face was drawn on that plane and with that
brush's texture, and the facet's own surface not at all.

So the brushes of the leaves one portal from the solid leaf are asked too, under the same 0.1 bound and only for a side
facing out of it through the portal; one is taken only when its side lies nearer the portal than the side FindPortalSide
chose (or there was none) by more than PLANESIDE_EPSILON. Two sides nearer than that to one another are one surface to
the BSP, and taking the nearer of them would let rounding pick the face's plane: it did, on a macOS arm64 build (fused
multiply-add), where it moved the strip of testmapsQ2.curvedWallPortalSplitLine onto another node. Otherwise nothing
changes.
============
*/
static void FindNearerSideOneLeafAway(portal_t *p, const contentflags_t &viscontents, side_t *bestside[2])
{
    const qvec3d &normal = p->onnode->get_nodedata()->get_plane().get_normal();
    double nearest[2];
    for (int i = 0; i < 2; ++i) {
        nearest[i] = bestside[i] ? p->winding.max_dist_off_plane(bestside[i]->get_plane())
                                 : std::numeric_limits<double>::infinity();
    }
    for (int j = 0; j < 2; j++) {
        node_t *n = p->nodes[j];
        if (n->get_leafdata()->contents.is_empty()) {
            continue;
        }
        int s;
        for (portal_t *q = n->portals; q; q = q->next[!s]) {
            s = (q->nodes[0] == n);
            node_t *m = q->nodes[s];
            if (q == p || !m->is_leaf() || m->get_leafdata()->contents.is_empty()) {
                continue;
            }
            for (auto it = m->get_leafdata()->original_brushes.rbegin(); it != m->get_leafdata()->original_brushes.rend();
                ++it) {
                auto *brush = *it;
                if (!contentflags_t::portal_generates_face(viscontents, brush->contents, SIDE_FRONT)) {
                    continue;
                }
                for (auto &side : brush->sides) {
                    if (side.bevel) {
                        continue;
                    }
                    // facing out of leaf j through the portal: nodes[0] is on the plane's front
                    if (qv::dot(normal, side.get_plane().get_normal()) * (j == 1 ? 1.0 : -1.0) <= 0) {
                        continue;
                    }
                    const double dist = p->winding.max_dist_off_plane(side.get_plane());
                    if (dist <= 0.1 && dist + PLANESIDE_EPSILON < nearest[!j]) {
                        nearest[!j] = dist;
                        bestside[!j] = &side;
                    }
                }
            }
        }
    }
}

/*
============
FindPortalSide

Finds a brush side to use for texturing the given portal
============
*/
static void FindPortalSide(portal_t *p, visible_faces_stats_t &stats)
{
    // decide which content change is strongest
    // solid > lava > water, etc

    // if either is "_noclipfaces" then we don't require a content change
    contentflags_t viscontents = contentflags_t::portal_visible_contents(
        p->nodes[0]->get_leafdata()->contents, p->nodes[1]->get_leafdata()->contents);
    if (viscontents.is_empty())
        return;

    // bestside[0] is the brushside visible on portal side[0] which is the positive side of the plane, always
    side_t *bestside[2] = {nullptr, nullptr};
    side_t *exactside[2] = {nullptr, nullptr};
    float bestdot = 0;
    const qbsp_plane_t &p1 = p->onnode->get_nodedata()->get_plane();

    // check brushes on both sides of the portal
    for (int j = 0; j < 2; j++) {
        node_t *n = p->nodes[j];

        // iterate the n->original_brushes vector in reverse order, so later brushes
        // in the map file order are prioritized
        for (auto it = n->get_leafdata()->original_brushes.rbegin(); it != n->get_leafdata()->original_brushes.rend();
            ++it) {
            auto *brush = *it;
            const bool generate_outside_face =
                contentflags_t::portal_generates_face(viscontents, brush->contents, SIDE_FRONT);
            const bool generate_inside_face =
                contentflags_t::portal_generates_face(viscontents, brush->contents, SIDE_BACK);

            if (!(generate_outside_face || generate_inside_face)) {
                continue;
            }

            for (auto &side : brush->sides) {
                if (side.bevel)
                    continue;

                if (p->winding.max_dist_off_plane(side.get_plane()) > 0.1) {
                    continue;
                }

                if ((side.planenum & ~1) == p->onnode->get_nodedata()->planenum) {
                    // exact match (undirectional)

                    // because the brush is on j of the positive plane, the brushside must be facing away from j
                    Q_assert((side.planenum & 1) == !j);

                    // see which way(s) we want to generate faces - we could be a brush on either side of
                    // the portal, generating either a outward face (common case) or an inward face (liquids) or both.
                    if (generate_outside_face) {
                        // since we are iterating the brushes from highest priority (last) to lowest, take the first
                        // exactside we find
                        if (!exactside[!j]) {
                            exactside[!j] = &side;
                        }
                    }
                    if (generate_inside_face) {
                        if (!exactside[j]) {
                            exactside[j] = &side;
                        }
                    }

                    break;
                }
                // see how close the match is
                const auto &p2 = side.get_positive_plane();
                double dot = qv::dot(p1.get_normal(), p2.get_normal());

                // HACK: both the node plane and side.get_positive_plane() are supposed to be "positive", but the
                // which would imply dot >= 0, but the meaning of "positive" is ambiguous on 45 degree planes.
                // so take the absolute value to work around that case (this is an undirectional test anyway).
                dot = std::fabs(dot);

                if (dot > bestdot) {
                    bestdot = dot;
                    if (generate_outside_face) {
                        bestside[!j] = &side;
                    }
                    if (generate_inside_face) {
                        bestside[j] = &side;
                    }
                }
            }
        }
    }

    if (!exactside[0] && !exactside[1]) {
        FindNearerSideOneLeafAway(p, viscontents, bestside);
    }

    // take exact sides over best sides
    for (int i = 0; i < 2; ++i) {
        if (exactside[i]) {
            bestside[i] = exactside[i];
        }
    }

    if (!bestside[0] && !bestside[1]) {
        stats.sides_not_found++;
        logging::print(logging::flag::VERBOSE, "couldn't find portal side at {}\n", p->winding.center());
        stats.missing_portal_sides.push_back(p->winding.clone());
    }

    p->sidefound = true;

    for (int i = 0; i < 2; ++i) {
        p->sides[i] = bestside[i];
    }
}

/*
===============
MarkVisibleSides_r

===============
*/
static void MarkVisibleSides_r(node_t *node, visible_faces_stats_t &stats)
{
    if (auto *nodedata = node->get_nodedata()) {
        MarkVisibleSides_r(nodedata->children[0], stats);
        MarkVisibleSides_r(nodedata->children[1], stats);
        return;
    }

    auto *leafdata = node->get_leafdata();

    // empty leafs are never boundary leafs
    if (leafdata->contents.is_empty())
        return;

    // see if there is a visible face
    int s;
    for (portal_t *p = node->portals; p; p = p->next[!s]) {
        s = (p->nodes[0] == node);
        if (!p->onnode)
            continue; // edge of world
        if (!p->sidefound) {
            FindPortalSide(p, stats);
        }
        for (int i = 0; i < 2; ++i) {
            if (p->sides[i] && p->sides[i]->source) {
                p->sides[i]->source->visible = true;
                stats.sides_visible++;
            }
        }
    }
}

/*
=============
MarkVisibleSides

=============
*/
void MarkVisibleSides(tree_t &tree, bspbrush_t::container &brushes)
{
    logging::funcheader();

    // clear all the visible flags
    MarkBrushSidesInvisible(brushes);

    visible_faces_stats_t stats;
    // set visible flags on the sides that are used by portals
    MarkVisibleSides_r(tree.headnode, stats);

    // and by the draw-only portals, examined as MarkVisibleSides_r examines a linked one: from a leaf that is not
    // empty
    for (auto &p : tree.draw_only_portals) {
        if (p->fill_only) {
            continue;
        }
        if (p->nodes[0]->get_leafdata()->contents.is_empty() && p->nodes[1]->get_leafdata()->contents.is_empty()) {
            continue;
        }
        FindPortalSide(p.get(), stats);
        for (int i = 0; i < 2; ++i) {
            if (p->sides[i] && p->sides[i]->source) {
                p->sides[i]->source->visible = true;
                stats.sides_visible++;
            }
        }
    }

    if (!stats.missing_portal_sides.empty() && qbsp_options.debug_missing_portal_sides.value()) {
        fs::path name = qbsp_options.bsp_path;
        name.replace_extension("missing_portal_sides.prt");
        WriteDebugPortals(stats.missing_portal_sides, name);
    }
}
