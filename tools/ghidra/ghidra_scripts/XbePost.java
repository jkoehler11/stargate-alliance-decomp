// Name XBE functions from the evidence the build ships: assert strings first, then the script
// side's own native table. Emits a symbol map for every function.
//
// MSVC-era asserts embed __FILE__, so each assert site belongs to the function containing it. That
// gives hundreds of functions a module+source-file attribution for free (PLAN 2.3 / 4.2).
//
// The GNatives pass (PLAN 4.4) is the other half: slot N of the native dispatch table is the
// function the script calls as class.function at index N, so the table names code that nothing
// calls directly -- without it those functions stay FUN_00xxxxxx forever, because no xref leads
// to them. Slots with no declarer stay unnamed (only commented), because an index is evidence of a
// dispatch entry, not of a function name.
// @category SG1
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.CommentType;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.symbol.SourceType;
import java.io.*;
import java.util.*;

public class XbePost extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            println("usage: XbePost.java <asserts.tsv> <out.csv> [entryVa] [gnatives.tsv] [gnatives_out.csv] [overrides.csv]");
            return;
        }
        // Make sure the entry point became a function; on an XBE nothing else marks it.
        if (args.length > 2) {
            Address entry = currentProgram.getAddressFactory().getDefaultAddressSpace()
                    .getAddress(Long.decode(args[2]));
            if (getFunctionAt(entry) == null) {
                disassemble(entry);
                createFunction(entry, "XbeEntry");
                println("created entry function at " + entry);
            }
        }

        Map<Long, String[]> asserts = new HashMap<>();   // xref address -> {module, file, stringVa}
        BufferedReader reader = new BufferedReader(new FileReader(args[0]));
        String line;
        while ((line = reader.readLine()) != null) {
            if (line.startsWith("#") || line.trim().isEmpty()) continue;
            String[] p = line.split("\t");
            if (p.length < 4) continue;
            asserts.put(Long.decode(p[3]), new String[] { p[0], p[1], p[2] });
        }
        reader.close();

        Map<String, Integer> perModule = new TreeMap<>();
        int named = 0, unresolved = 0, duplicates = 0;
        for (Map.Entry<Long, String[]> e : asserts.entrySet()) {
            Address at = currentProgram.getAddressFactory().getDefaultAddressSpace()
                    .getAddress(e.getKey());
            Function fn = getFunctionContaining(at);
            if (fn == null) { unresolved++; continue; }
            if (fn.getName().startsWith("FUN_") || fn.getName().startsWith("sub_")) {
                String module = e.getValue()[0].replace('-', '_').replace("?", "unknown");
                String file = e.getValue()[1].replace(".cpp", "").replace(".h", "")
                        .replaceAll("[^A-Za-z0-9_]", "_");
                String name = module + "_" + file;
                // Two asserts in the same function must not fight over the name: the first wins and
                // the duplicate is counted, not silently renamed.
                try {
                    fn.setName(name, SourceType.USER_DEFINED);
                    named++;
                } catch (Exception duplicate) {
                    duplicates++;
                }
                perModule.merge(module, 1, Integer::sum);
            }
        }

        // GNatives: name the dispatch targets from the script side's native declarations. Every
        // slot is evidence; only a slot with a declarer carries a name.
        int nativeNamed = 0, nativeCreated = 0, nativeKept = 0, nativeClash = 0, slotComment = 0;
        PrintWriter nout = null;
        if (args.length > 3) {
            if (args.length > 4) nout = new PrintWriter(new FileWriter(args[4]));
            if (nout != null) nout.println("index,address,action,name");
            Listing listing = currentProgram.getListing();
            BufferedReader gn = new BufferedReader(new FileReader(args[3]));
            String gl;
            while ((gl = gn.readLine()) != null) {
                if (gl.startsWith("#") || gl.trim().isEmpty()) continue;
                // Keep trailing empty fields: a slot-only row has an empty name column.
                String[] p = gl.split("\t", -1);
                if (p.length < 4) continue;
                String index = p[0];
                String name = p[2];
                String evidence = p[3];
                Address at = currentProgram.getAddressFactory().getDefaultAddressSpace()
                        .getAddress(Long.decode(p[1]));
                if (at == null) continue;
                Function fn = getFunctionAt(at);
                String action;
                if (fn == null) {
                    // Nothing xrefs a dispatch target, so the analyser never made it a function.
                    disassemble(at);
                    fn = createFunction(at, name.isEmpty() ? "Native_" + index : name);
                    if (fn == null) {
                        if (nout != null) nout.println(index + ",0x" + p[1] + ",failed,");
                        continue;
                    }
                    nativeCreated++;
                    action = "created";
                } else if (name.isEmpty()) {
                    action = "slot-only";
                } else if (fn.getName().startsWith("FUN_") || fn.getName().startsWith("sub_")) {
                    try {
                        fn.setName(name, SourceType.USER_DEFINED);
                        nativeNamed++;
                        action = "renamed";
                    } catch (Exception clash) {
                        nativeClash++;
                        action = "name-clash";
                    }
                } else {
                    // Already named from an assert: the slot mapping is recorded, the name stands.
                    nativeKept++;
                    action = "already-named";
                }
                if (evidence.equals("slot-only") && fn != null
                        && listing.getComment(CommentType.PLATE, at) == null) {
                    listing.setComment(at, CommentType.PLATE,
                            "GNatives[" + index + "] -- dispatch slot with no declarer in the shipped packages");
                    slotComment++;
                }
                if (nout != null) {
                    nout.println(index + ",0x" + p[1] + "," + action + ","
                            + (fn == null ? "" : fn.getName()));
                }
            }
            gn.close();
            if (nout != null) nout.close();
        }

        // Explicit overrides last, so a stronger piece of evidence wins over the assert-derived file
        // name: symbols/xbe_overrides.csv carries names derived from the build's own data (e.g. the
        // class literal inside a ucpp registration stub) with the evidence recorded per row.
        int overridden = 0;
        if (args.length > 5) {
            BufferedReader ov = new BufferedReader(new FileReader(args[5]));
            String ol;
            while ((ol = ov.readLine()) != null) {
                if (ol.startsWith("address") || ol.startsWith("#") || ol.trim().isEmpty()) continue;
                String[] p = ol.split(",", -1);
                if (p.length < 2) continue;
                Address at = currentProgram.getAddressFactory().getDefaultAddressSpace()
                        .getAddress(Long.decode(p[0]));
                Function fn = at == null ? null : getFunctionAt(at);
                if (fn == null || fn.getName().equals(p[1])) continue;
                try {
                    fn.setName(p[1], SourceType.USER_DEFINED);
                    overridden++;
                } catch (Exception ignored) {
                    // duplicate name: the override file is reviewed, so a clash is a name bug
                }
            }
            ov.close();
        }

        int functions = currentProgram.getFunctionManager().getFunctionCount();
        int unnamed = 0;
        for (Function fn : currentProgram.getFunctionManager().getFunctions(true)) {
            if (fn.getName().startsWith("FUN_")) unnamed++;
        }
        PrintWriter out = new PrintWriter(new FileWriter(args[1]));
        out.println("address,name,size,module");
        for (Function fn : currentProgram.getFunctionManager().getFunctions(true)) {
            out.println(String.format("0x%08x,%s,%d,",
                    fn.getEntryPoint().getOffset(), fn.getName(), fn.getBody().getNumAddresses()));
        }
        out.close();
        println("functions: " + functions + ", named from asserts: " + named
                + ", duplicate names: " + duplicates + ", xrefs in no function: " + unresolved);
        println("overrides applied: " + overridden);
        println("gnatives: renamed " + nativeNamed + ", created " + nativeCreated
                + ", already named " + nativeKept + ", name clashes " + nativeClash
                + ", slot comments " + slotComment + ", still FUN_* " + unnamed);
        for (Map.Entry<String, Integer> e : perModule.entrySet()) {
            println("  module " + e.getKey() + ": " + e.getValue() + " assert-anchored functions");
        }
    }
}
