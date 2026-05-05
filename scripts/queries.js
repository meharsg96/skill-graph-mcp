// mongosh examples — run: mongosh < scripts/queries.js
use("skill_graph");

print("\n=== Active skills ===");
db.skills.find({ lifecycle: "active" }, { name: 1, input_type: 1, output_type: 1 })
  .forEach(s => print(`  ${s.name}: ${s.input_type} -> ${s.output_type}`));

print("\n=== $graphLookup: dependency closure from query-analysis (v1) ===");
db.skills.aggregate([
  { $match: { _id: "skill:query-analysis" } },
  { $graphLookup: {
      from: "skills",
      startWith: "$_id",
      connectFromField: "_id",
      connectToField: "dependencies",
      as: "downstream",
      maxDepth: 10,
      depthField: "depth",
      restrictSearchWithMatch: { lifecycle: "active" }
  }},
  { $project: {
      entry: "$name",
      chain: {
        $map: {
          input: { $sortArray: { input: "$downstream", sortBy: { depth: 1 } } },
          as: "s",
          in: { name: "$$s.name", input: "$$s.input_type", output: "$$s.output_type", depth: "$$s.depth" }
        }
      }
  }}
]).forEach(r => {
  print(`  Entry: ${r.entry}`);
  r.chain.forEach(s => print(`    depth ${s.depth}: ${s.name} (${s.input} -> ${s.output})`));
});

print("\n=== Type mismatches in edges ===");
db.edges.find({ compatible: false }).forEach(e => {
  print(`  ${e.from_skill} -> ${e.to_skill}: ${e.produced_type} != ${e.consumed_type}`);
});

print("\n=== Targeted query: one skill's domain_fields ===");
const ui = db.skills.findOne({ _id: "skill:ui-builder" }, { name: 1, domain_fields: 1 });
print(`  ${ui.name}:`);
print(`  themes:      ${Object.keys(ui.domain_fields.design_tokens.themes)}`);
print(`  breakpoints: ${JSON.stringify(ui.domain_fields.breakpoints)}`);

print("\n=== v2: route_task — backward $graphLookup from a target output ===");
db.skills.aggregate([
  { $match: { "output.type": "ui_components", lifecycle: "active" } },
  { $limit: 1 },
  { $graphLookup: {
      from: "skills",
      startWith: "$dependencies",
      connectFromField: "dependencies",
      connectToField: "_id",
      as: "prerequisites",
      maxDepth: 10,
      depthField: "depth",
      restrictSearchWithMatch: { lifecycle: "active" }
  }},
  { $project: {
      target: "$name",
      chain: {
        $map: {
          input: { $sortArray: { input: "$prerequisites", sortBy: { depth: -1 } } },
          as: "s",
          in: { name: "$$s.name", depth: "$$s.depth" }
        }
      }
  }}
]).forEach(r => {
  print(`  target: ${r.target}`);
  r.chain.forEach(s => print(`    dep depth ${s.depth}: ${s.name}`));
});

print("\n=== v2: impact_analysis — direct + transitive consumers of schema-review ===");
const target = "skill:schema-review";
const targetDoc = db.skills.findOne({ _id: target });
print(`  ${target} produces: ${targetDoc.output_type}`);
print("  direct consumers:");
db.skills.find(
  { input_type: targetDoc.output_type, lifecycle: "active", _id: { $ne: target } },
  { name: 1 }
).forEach(s => print(`    ${s.name}`));
print("  transitive downstream:");
db.skills.aggregate([
  { $match: { _id: target } },
  { $graphLookup: {
      from: "skills",
      startWith: "$_id",
      connectFromField: "_id",
      connectToField: "dependencies",
      as: "downstream",
      maxDepth: 10,
      depthField: "depth",
      restrictSearchWithMatch: { lifecycle: "active" }
  }}
]).forEach(r => {
  r.downstream.sort((a, b) => a.depth - b.depth).forEach(s =>
    print(`    depth ${s.depth}: ${s.name}`)
  );
});
print("  incompatible edges involving this skill:");
db.edges.find({ compatible: false, $or: [{ from_skill: target }, { to_skill: target }] })
  .forEach(e => print(`    ${e.from_skill} -> ${e.to_skill}: ${e.note || ""}`));

print("\n=== v2: tenant parameters ===");
db.parameters.find({}, { _id: 1, tenant: 1, "design_tokens.themes.dark.primary": 1, component_overrides: 1 })
  .forEach(p => print(`  ${p._id}  primary(dark)=${p.design_tokens.themes.dark.primary}  overrides=${JSON.stringify(p.component_overrides)}`));

print("\n=== Instrumentation: db.runs aggregation (per-tool token efficiency) ===");
db.runs.aggregate([
  { $group: { _id: "$tool", calls: { $sum: 1 }, avg_tokens: { $avg: "$tokens_returned" } } },
  { $sort: { avg_tokens: -1 } }
]).forEach(r => print(`  ${r._id.padEnd(25)}  calls=${r.calls}  avg_tokens=${Math.round(r.avg_tokens)}`));

print("\nDone.");
