using System.Globalization;
using UELib;
using UELib.Core;
using UELib.Flags;

namespace Sg1Tool;

/// <summary>
/// Package/class/native inventory. Every number here comes from the package tables, so the output
/// can be diffed against the plan and against later tooling; nothing is inferred.
/// </summary>
internal static class InventoryCommand
{
    public static int Run(string dumpRoot, string outDir, string? packageFilter)
    {
        var packages = Dump.Packages(dumpRoot).ToList();
        if (!string.IsNullOrWhiteSpace(packageFilter))
            packages = packages
                .Where(p => Path.GetFileNameWithoutExtension(p)
                    .Equals(packageFilter, StringComparison.OrdinalIgnoreCase))
                .ToList();
        if (packages.Count == 0)
            throw new InvalidOperationException($"no .u packages matched under {dumpRoot}");

        var packageRows = new List<object?[]>();
        var classRows = new List<object?[]>();
        var nativeRows = new List<object?[]>();
        var propertyRows = new List<object?[]>();
        var failures = new List<object?[]>();

        foreach (var path in packages)
        {
            var name = Path.GetFileNameWithoutExtension(path);
            Console.Error.WriteLine($"inventory: {name}");
            UnrealPackage package;
            try
            {
                package = Dump.Load(path);
            }
            catch (Exception ex)
            {
                failures.Add(new object?[] { name, "load", ex.GetType().Name, ex.Message });
                Console.Error.WriteLine($"  FAILED: {ex.Message}");
                continue;
            }

            var exports = package.Objects.Where(Dump.IsExport).ToList();
            var classes = exports.OfType<UClass>().ToList();
            var functions = exports.OfType<UFunction>().ToList();
            var states = exports.OfType<UState>().ToList();
            var structs = exports.OfType<UScriptStruct>().ToList();
            var enums = exports.OfType<UEnum>().ToList();
            var consts = exports.OfType<UConst>().ToList();
            var props = exports.OfType<UProperty>().ToList();
            var nativeFunctions = functions
                .Where(f => f.FunctionFlags.HasFlag(FunctionFlag.Native)).ToList();

            var summary = package.Summary;
            packageRows.Add(new object?[]
            {
                name,
                new FileInfo(path).Length,
                package.Build?.Name.ToString() ?? "?",
                summary.Version,
                summary.LicenseeVersion,
                summary.EngineVersion,
                FlagsText.Of(summary.PackageFlags),
                summary.NameCount,
                summary.ExportCount,
                summary.ImportCount,
                classes.Count,
                functions.Count,
                nativeFunctions.Count,
                states.Count,
                structs.Count,
                enums.Count,
                consts.Count,
                props.Count
            });

            foreach (var cls in classes)
            {
                var ownFunctions = functions.Count(f => f.Outer == cls);
                var ownNatives = nativeFunctions.Count(f => f.Outer == cls);
                classRows.Add(new object?[]
                {
                    name,
                    cls.Name.ToString(),
                    cls.Super?.Name.ToString() ?? "",
                    // UE2 has no plain CLASS_Native bit: a class is native-backed iff the compiler
                    // emitted native functions for it. NativeOnly is recorded separately below.
                    ownNatives > 0,
                    FlagsText.Of(cls.ClassFlags),
                    cls.HasClassFlag(ClassFlags.NativeOnly),
                    cls.Super?.Outer?.Name.ToString() ?? "",
                    ownFunctions,
                    ownNatives,
                    states.Count(s => s.Outer == cls),
                    props.Count(p => p.Outer == cls),
                    structs.Count(s => s.Outer == cls),
                    enums.Count(e => e.Outer == cls),
                    consts.Count(c => c.Outer == cls),
                    cls.EnumerateSuper().Count()-1 // inheritance depth, 0 = extends Object directly below Core.Object
                });
            }

            foreach (var fn in nativeFunctions)
            {
                nativeRows.Add(new object?[]
                {
                    name,
                    Dump.OuterClassName(fn),
                    fn.Name.ToString(),
                    fn.NativeToken,
                    fn.EnumerateFields().OfType<UProperty>().Count(p => p.IsParm()),
                    FlagsText.Of(fn.FunctionFlags)
                });
            }

            foreach (var prop in props.Where(p => p.Outer is UClass))
            {
                propertyRows.Add(new object?[]
                {
                    name,
                    prop.Outer!.Name.ToString(),
                    prop.Name.ToString(),
                    prop.GetType().Name,
                    FlagsText.Of(prop.PropertyFlags),
                    prop is UProperty p2 ? p2.GetFriendlyType().ToString() : ""
                });
            }

            // Anything the reader could construct but not fully deserialize is a real finding --
            // it usually means a fork-specific field, which matters for the script track.
            foreach (var obj in exports)
            {
                try
                {
                    obj.BeginDeserializing();
                }
                catch (Exception ex)
                {
                    failures.Add(new object?[] { name, obj.GetReferencePath(), ex.GetType().Name, ex.Message });
                }
            }
        }

        Csv.Write(Path.Combine(outDir, "packages.csv"),
            new[]
            {
                "package", "bytes", "uelib_build", "version", "licensee_version", "engine_version",
                "package_flags", "name_count", "export_count", "import_count", "class_exports",
                "function_exports", "native_function_exports", "state_exports", "struct_exports",
                "enum_exports", "const_exports", "property_exports"
            },
            packageRows.OrderBy(r => Csv.SortKey(r[0]), StringComparer.Ordinal));

        Csv.Write(Path.Combine(outDir, "classes.csv"),
            new[]
            {
                "package", "class", "super", "is_native", "class_flags", "native_only",
                "package_of_super", "functions", "native_functions", "states", "properties", "structs", "enums", "consts",
                "super_depth"
            },
            classRows.OrderBy(r => Csv.SortKey(r[0]), StringComparer.Ordinal)
                .ThenBy(r => Csv.SortKey(r[1]), StringComparer.Ordinal));

        Csv.Write(Path.Combine(outDir, "natives.csv"),
            new[] { "package", "class", "function", "native_token", "parameter_count", "function_flags" },
            nativeRows.OrderBy(r => Csv.SortKey(r[0]), StringComparer.Ordinal)
                .ThenBy(r => Convert.ToInt32(r[3], CultureInfo.InvariantCulture))
                .ThenBy(r => Csv.SortKey(r[1]), StringComparer.Ordinal));

        Csv.Write(Path.Combine(outDir, "class_properties.csv"),
            new[] { "package", "class", "property", "kind", "property_flags", "friendly_type" },
            propertyRows.OrderBy(r => Csv.SortKey(r[0]), StringComparer.Ordinal)
                .ThenBy(r => Csv.SortKey(r[1]), StringComparer.Ordinal)
                .ThenBy(r => Csv.SortKey(r[2]), StringComparer.Ordinal));

        Csv.Write(Path.Combine(outDir, "package_parse.csv"),
            new[] { "package", "object", "exception", "message" }, failures);

        Console.Error.WriteLine(
            $"inventory: {packageRows.Count} packages, {classRows.Count} classes, " +
            $"{nativeRows.Count} native functions, {failures.Count} errors -> {outDir}");
        return 0;
    }

}
