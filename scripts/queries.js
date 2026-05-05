// mongosh examples — run: mongosh < scripts/queries.js
use("skill_graph");

print("\n=== Active skills ===");
db.skills.find({ lifecycle: "active" }, { name: 1, input_type: 1, output_type: 1 })
  .forEach(s => print(`  ${s.name}: ${s.input_type} -> ${s.output_type}`));

print("\n=== $graphLookup: dependency closure from query-analysis ===");
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
print(`  tokens: ${JSON.stringify(ui.domain_fields.design_tokens)}`);
print(`  breakpoints: ${JSON.stringify(ui.domain_fields.breakpoints)}`);

print("\nDone.");
