// Carve an original-Xbox XBE into its real sections before analysis.
// Ghidra's binary loader imports the file as one flat block, but XBE file offsets and virtual
// addresses diverge after the first section, so every address past .text would be wrong.
// @category SG1
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSpace;
import ghidra.program.model.mem.*;
import java.io.*;

public class XbePre extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            println("usage: XbePre.java <sections.tsv> <xbe-file>");
            return;
        }
        Memory mem = currentProgram.getMemory();
        AddressSpace space = currentProgram.getAddressFactory().getDefaultAddressSpace();

        // Read the whole file once; sections are slices of it.
        RandomAccessFile raf = new RandomAccessFile(args[1], "r");
        byte[] file = new byte[(int) raf.length()];
        raf.readFully(file);
        raf.close();

        // The binary loader created one flat block covering the whole file; every real section would
        // collide with it, so drop it before carving.
        java.util.List<MemoryBlock> existing = new java.util.ArrayList<>();
        for (MemoryBlock b : mem.getBlocks()) existing.add(b);
        for (MemoryBlock b : existing) {
            mem.removeBlock(b, monitor);
            println("removed loader block: " + b.getName());
        }

        BufferedReader reader = new BufferedReader(new FileReader(args[0]));
        String line;
        int created = 0;
        while ((line = reader.readLine()) != null) {
            line = line.trim();
            if (line.isEmpty() || line.startsWith("#")) continue;
            String[] p = line.split("\t");
            String name = p[0];
            long va = Long.decode(p[1]);
            long vsize = Long.decode(p[2]);
            long foff = Long.decode(p[3]);
            long fsize = Long.decode(p[4]);
            long flags = Long.decode(p[5]);
            boolean exec = (flags & 0x4) != 0;
            boolean write = (flags & 0x1) != 0;
            Address start = space.getAddress(va);
            MemoryBlock block = mem.createInitializedBlock(name, start, Math.max(vsize, 1), (byte) 0,
                    monitor, false);
            long copy = Math.min(fsize, Math.min(vsize, file.length - foff));
            if (copy > 0) {
                byte[] slice = new byte[(int) copy];
                System.arraycopy(file, (int) foff, slice, 0, (int) copy);
                mem.setBytes(start, slice);
            }
            block.setRead(true);
            block.setWrite(write);
            block.setExecute(exec);
            created++;
            println(String.format("block %-12s va=%08x size=%08x exec=%b write=%b", name, va, vsize, exec, write));
        }
        reader.close();

        println("sections created: " + created);
        // Analysis entry point: the XBE header records it; pass it as an argument so the script is
        // not tied to one build.
        if (args.length > 2) {
            Address entry = space.getAddress(Long.decode(args[2]));
            println("entry point: " + entry + " (analysis will start here)");
            setAnalysisOption(currentProgram, "Disassemble Entry Points", "true");
        }
    }
}
